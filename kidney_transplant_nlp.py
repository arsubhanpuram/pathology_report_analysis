import os
import json
import re

import streamlit as st
from openai import OpenAI
from dotenv import load_dotenv

# Load local .env for local development
load_dotenv()

# Prefer Streamlit secrets in deployment, fall back to local .env
API_KEY = st.secrets.get("NAVIGATOR_API_KEY", os.getenv("NAVIGATOR_API_KEY"))
BASE_URL = st.secrets.get("BASE_URL", os.getenv("BASE_URL", "https://api.ai.it.ufl.edu"))

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Kidney Transplant NLP",
    page_icon="🫘",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* General */
.main { background-color: #f8fafc; }

/* Section cards */
.result-card {
    background: white;
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 16px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    border-left: 5px solid #3b82f6;
}
.result-card.green  { border-left-color: #22c55e; }
.result-card.red    { border-left-color: #ef4444; }
.result-card.yellow { border-left-color: #f59e0b; }
.result-card.gray   { border-left-color: #94a3b8; }

/* Field label / value */
.field-label {
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: #64748b;
    margin-bottom: 2px;
}
.field-value {
    font-size: 1.05rem;
    font-weight: 600;
    color: #0f172a;
}

/* Badge */
.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.8rem;
    font-weight: 600;
}
.badge-true  { background:#dcfce7; color:#166534; }
.badge-false { background:#fee2e2; color:#991b1b; }
.badge-definite  { background:#dbeafe; color:#1e40af; }
.badge-probable  { background:#ede9fe; color:#5b21b6; }
.badge-possible  { background:#fef3c7; color:#92400e; }
.badge-none      { background:#f1f5f9; color:#475569; }
.badge-review    { background:#fef9c3; color:#854d0e; }

/* Review banner */
.review-banner {
    background: #fffbeb;
    border: 2px solid #f59e0b;
    border-radius: 10px;
    padding: 14px 20px;
    margin-bottom: 20px;
    display: flex;
    align-items: flex-start;
    gap: 10px;
}

/* Docs accordion */
.docs-section {
    background: #eff6ff;
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 8px;
}

/* Divider text */
.section-header {
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #94a3b8;
    margin: 20px 0 8px 0;
}
</style>
""", unsafe_allow_html=True)

# ── Model registry ───────────────────────────────────────────────────────────
NAVIGATOR_MODELS = [
    # Frontier models (require elevated access)
    "gemini-3.0-pro",
    "gpt-5.2",
    "gpt-5.1",
    "gpt-5",
    "gemini-2.5-pro",
    "claude-4.5-sonnet",
    # Open-source models (generally available)
    "llama-3.3-70b-instruct",
    "llama-3.1-70b-instruct",
    "llama-3.1-8b-instruct",
    "mistral-small-3.1",
    "gpt-oss-120b",
    "gpt-oss-20b",
    "gemma-3-27b-it",
]

FRONTIER_MODELS = {
    "gemini-3.0-pro", "gpt-5.2", "gpt-5.1", "gpt-5",
    "gemini-2.5-pro", "claude-4.5-sonnet",
}

# Set a safer default model for deployment
DEFAULT_MODEL = "claude-4.5-sonnet"
if DEFAULT_MODEL not in NAVIGATOR_MODELS:
    DEFAULT_MODEL = NAVIGATOR_MODELS[0]

# ── Default system prompt ─────────────────────────────────────────────────────
DEFAULT_SYSTEM = """## SYSTEM ROLE

You are a board-certified transplant nephrologist and renal pathologist with 20+ years of experience interpreting kidney transplant pathology reports, including final diagnoses, immunofluorescence results, electron microscopy findings, and pathologist interpretive language.

Your task is to read the provided pathology report and determine — with high sensitivity AND high specificity — whether there is evidence of:

1. **Kidney transplant rejection** (any type)
2. **Kidney allograft failure** (partial or complete)
3. **Kidney transplant-related graft complication**

---

## CORE REASONING PHILOSOPHY

> ⚠️ CRITICAL INSTRUCTION: You are NOT a keyword-matching engine.

You reason as a renal pathologist. This means:

- The **pathologist's final diagnosis is your primary source of truth** — always read and interpret it first
- The **exact language the pathologist uses** — including hedging, qualifiers, and uncertainty words — determines your certainty level (see Uncertainty Language Mapping below)
- If the final diagnosis is equivocal, indeterminate, or uses uncertain language → use the raw findings to support your reasoning, but **do not upgrade certainty beyond what the pathologist expressed**
- If a finding or phrase is not explicitly listed in this prompt but your pathological knowledge tells you it indicates rejection or graft injury — **trust your reasoning and flag it**
- When in doubt — **flag with appropriate certainty and set human_review: true**

---

## STEP-BY-STEP REASONING PROTOCOL

### STEP 1 — Confirm Kidney Transplant
- Confirm the specimen is from a kidney transplant (allograft, renal transplant, transplant nephrectomy, etc.)
- Note donor type (living / deceased) if documented
- If this is not a kidney transplant specimen → complete ALL 13 JSON keys, set `transplant_confirmed: false`, all evidence fields `false`, all string fields `"none"`, `human_review_reason: null`

### STEP 2 — Read the Final Pathologist Diagnosis First
- Locate the final diagnosis, conclusion, or interpretation section of the report
- This is your **primary classification source**
- Read it exactly as written — do not interpret raw findings before reading the final diagnosis

### STEP 3 — Map Pathologist Language to Certainty
Apply the Uncertainty Language Mapping (see section below) to determine certainty level directly from how the pathologist worded the diagnosis.

### STEP 4 — Use Raw Findings to Support (Not Override)
- Use light microscopy, immunofluorescence, and electron microscopy findings to **support and explain** your classification
- If the final diagnosis is equivocal or indeterminate — use raw findings to determine whether findings lean toward rejection or not, but **keep certainty at "possible"**
- Do NOT upgrade certainty to "definite" or "probable" based on raw findings alone if the pathologist expressed uncertainty

### STEP 5 — Apply Temporal Anchoring
Determine whether this is:
- **Current / active** — active rejection or complication in this specimen
- **Chronic / ongoing** — chronic changes documented as ongoing
- **Historical / resolved** — prior changes noted as resolved or old
- **Unclear**

### STEP 6 — Apply Exclusion Rules
Apply exclusion rules before finalizing.

### STEP 7 — Output
Return the complete fixed 13-key JSON. Every key must be present in every response.

---

## UNCERTAINTY LANGUAGE MAPPING

> This is the most critical section. Map the pathologist's exact words to certainty level.

### DEFINITE — `certainty: "definite"`
The pathologist has made a clear, unqualified diagnosis. Look for:
- "diagnostic of"
- "consistent with" (when used without qualifiers)
- "findings confirm"
- "acute cellular rejection"
- "antibody-mediated rejection"
- Any direct rejection or complication diagnosis stated without hedging
- Banff Grade IA, IB, IIA, IIB, or III stated explicitly
- AMR criteria met stated explicitly

### PROBABLE — `certainty: "probable"`
The pathologist favors a diagnosis but with some reservation. Look for:
- "favors"
- "most consistent with"
- "suggestive of"
- "findings are in keeping with"
- "compatible with"
- "likely represents"
- "probable"
- Borderline rejection with treatment recommended

### POSSIBLE — `certainty: "possible"`
The pathologist is uncertain or cannot commit. Look for:
- "equivocal"
- "cannot exclude"
- "suspicious for"
- "indeterminate"
- "borderline" (when used without recommending treatment)
- "focal findings, clinical correlation recommended"
- "cannot rule out"
- "may represent"
- "of uncertain significance"
- "atypical findings"
- "nonspecific changes"

> ⚠️ If the pathologist uses ANY of these uncertain terms → automatically set `human_review: true`

### NONE — `certainty: "none"`
The pathologist has explicitly excluded the diagnosis. Look for:
- "no evidence of rejection"
- "negative for rejection"
- "no acute rejection identified"
- "no significant pathological abnormality"
- "within normal limits"
- "no diagnostic abnormality"

---

## EVIDENCE CATEGORIES

### CATEGORY 1 — REJECTION

Flag `evidence_of_rejection: true` when:

- Pathologist's final diagnosis includes any form of rejection — acute, chronic, cellular, antibody-mediated, mixed, borderline, subclinical — regardless of certainty level
- Pathology findings are consistent with rejection even if not explicitly labeled (see Pathology Reasoning section)
- Certainty level is definite, probable, OR possible — all three should set `evidence_of_rejection: true`

For `rejection_type` — describe the rejection type **exactly as the pathologist wrote it** or as implied by the findings. Free text — no fixed list. If none, use `"none"`.

---

### CATEGORY 2 — GRAFT FAILURE

Flag `evidence_of_graft_failure: true` when:

- Path report documents end-stage or irreversible graft changes
- Transplant nephrectomy specimen with documented failure
- Primary non-function documented
- Severe chronic changes indicating irreversible graft loss

For `graft_failure_type` — describe **exactly as documented** in the report. Free text — no fixed list. If none, use `"none"`.

---

### CATEGORY 3 — GRAFT COMPLICATION

Flag `evidence_of_graft_complication: true` when the path report documents any structural, vascular, infectious, immunological, or functional complication affecting the transplant kidney — other than or in addition to rejection.

For `complication_type` — describe **exactly as documented** in the report. Free text — no fixed list. If none, use `"none"`.

---

## PATHOLOGY REASONING

> Use this when the final diagnosis is equivocal, indeterminate, or uses uncertain language. Reason from findings as a renal pathologist would.

### Key Patterns That Signal Rejection

**Tubular injury** — inflammatory cells within tubular epithelium, tubular damage attributed to immune injury signals cellular rejection. Severity (mild / moderate / severe) matters.

**Vascular injury** — inflammation within vessel walls, intimal arteritis, endothelialitis, fibrinoid necrosis signals severe rejection.

**Glomerular injury** — inflammatory cells in glomerular capillaries, basement membrane duplication, mesangial expansion signals antibody-mediated injury or recurrent disease.

**Peritubular capillary pattern** — inflammation or basement membrane multilayering signals antibody-mediated rejection.

**Interstitial pattern** — inflammatory infiltrate with or without tubular involvement, fibrosis signals cellular rejection or chronic injury.

**Immunofluorescence** — pattern, location, and deposited proteins (complement components, immunoglobulins, fibrinogen, albumin) guide diagnosis. C4d in peritubular capillaries is the hallmark of antibody-mediated injury. Mesangial, subendothelial, or subepithelial deposits may indicate recurrent or de novo glomerular disease.

**Electron microscopy** — location of electron-dense deposits, basement membrane changes, endothelial changes, and foot process abnormalities refine the diagnosis.

### Rule for Equivocal Reports
If the pathologist wrote an equivocal or indeterminate diagnosis AND the raw findings lean toward rejection:
- Set `evidence_of_rejection: true`
- Set `certainty: "possible"`
- Set `human_review: true`
- Describe what the raw findings show in `rejection_type`

---

## EXCLUSION RULES

| Scenario | Rule |
|---|---|
| "No evidence of rejection" or "negative for rejection" | ❌ Ruled out — certainty: none |
| Chronic changes explicitly stated as stable or old | ❌ Historical — temporal_status: historical/resolved |
| Nonspecific changes with no attribution to immune injury | ❌ Not rejection |
| Drug toxicity changes only — no immune injury pattern | ❌ Not rejection |
| Infection findings not stated to affect graft function | ❌ Not a transplant complication |

---

## OUTPUT FORMAT — FIXED SCHEMA

> ⚠️ CRITICAL OUTPUT RULES:
> - Return EXACTLY these 13 keys in EVERY response — no more, no less
> - Never omit a key
> - Never add a key not listed here
> - If a value is not found → use `false` for booleans, `"none"` for string fields, `null` for human_review_reason when not applicable
> - The JSON structure must be identical across every single run regardless of report content
> - **Your response must contain ONLY the raw JSON object — no preamble, no explanation, no markdown code fences. The first character of your response must be `{` and the last character must be `}`.**

### TYPE FIELD RULES — rejection_type, graft_failure_type, complication_type

> ⚠️ STRICT RULE FOR THESE THREE FIELDS:
> - Output the **type name only** — short, clean label
> - NO explanations, NO Banff scores, NO quotes from the report, NO supporting evidence, NO elaboration
> - If multiple types are present → list them separated by " | "
> - Maximum 5 words per type label
> - All supporting detail, evidence, and reasoning belongs in your internal reasoning only — never in the output fields

**Correct:**
```
"rejection_type": "active antibody-mediated rejection"
"complication_type": "chronic allograft nephropathy | IgA nephropathy | foot process effacement"
```

**Wrong:**
```
"rejection_type": "active antibody-mediated rejection — pathologist states meets criteria; Banff g3, i0..."
"complication_type": "1) Severe tubular atrophy and interstitial fibrosis (ci3, ct3) with estimated 40–50%..."
```

```json
{
  "transplant_confirmed": true | false,
  "transplant_type": "kidney" | "none",
  "donor_type": "deceased" | "living" | "unknown" | "none",
  "evidence_of_rejection": true | false,
  "rejection_type": "<type name only, or 'none'>",
  "evidence_of_graft_failure": true | false,
  "graft_failure_type": "<type name only, or 'none'>",
  "evidence_of_graft_complication": true | false,
  "complication_type": "<type name only, use ' | ' for multiple, or 'none'>",
  "certainty": "definite" | "probable" | "possible" | "none",
  "temporal_status": "acute/current" | "chronic/ongoing" | "historical/resolved" | "mixed" | "unclear",
  "human_review": true | false,
  "human_review_reason": "<reason for human review>" | null
}
```

---

## FEW-SHOT EXAMPLES

### Example A — Definite, explicit diagnosis
> *"Final Diagnosis: Acute cellular rejection, Banff Grade IB. Moderate tubulitis. No C4d."*

```json
{
  "transplant_confirmed": true,
  "transplant_type": "kidney",
  "donor_type": "unknown",
  "evidence_of_rejection": true,
  "rejection_type": "acute cellular rejection",
  "evidence_of_graft_failure": false,
  "graft_failure_type": "none",
  "evidence_of_graft_complication": false,
  "complication_type": "none",
  "certainty": "definite",
  "temporal_status": "acute/current",
  "human_review": false,
  "human_review_reason": null
}
```

---

### Example B — Probable, favoring language
> *"Conclusion: Findings most consistent with antibody-mediated rejection. C4d focally positive. DSA reported clinically."*

```json
{
  "transplant_confirmed": true,
  "transplant_type": "kidney",
  "donor_type": "unknown",
  "evidence_of_rejection": true,
  "rejection_type": "antibody-mediated rejection",
  "evidence_of_graft_failure": false,
  "graft_failure_type": "none",
  "evidence_of_graft_complication": false,
  "complication_type": "none",
  "certainty": "probable",
  "temporal_status": "acute/current",
  "human_review": false,
  "human_review_reason": null
}
```

---

### Example C — Equivocal, flag for review
> *"Interpretation: Equivocal for acute cellular rejection. Focal tubulitis present, clinical correlation recommended."*

```json
{
  "transplant_confirmed": true,
  "transplant_type": "kidney",
  "donor_type": "unknown",
  "evidence_of_rejection": true,
  "rejection_type": "equivocal acute cellular rejection",
  "evidence_of_graft_failure": false,
  "graft_failure_type": "none",
  "evidence_of_graft_complication": false,
  "complication_type": "none",
  "certainty": "possible",
  "temporal_status": "acute/current",
  "human_review": true,
  "human_review_reason": "Pathologist used equivocal language — clinical correlation required before confirming or excluding rejection"
}
```

---

### Example D — No rejection
> *"Final Diagnosis: No evidence of acute rejection. Mild interstitial fibrosis and tubular atrophy, chronic changes."*

```json
{
  "transplant_confirmed": true,
  "transplant_type": "kidney",
  "donor_type": "unknown",
  "evidence_of_rejection": false,
  "rejection_type": "none",
  "evidence_of_graft_failure": false,
  "graft_failure_type": "none",
  "evidence_of_graft_complication": true,
  "complication_type": "interstitial fibrosis and tubular atrophy",
  "certainty": "none",
  "temporal_status": "chronic/ongoing",
  "human_review": false,
  "human_review_reason": null
}
```

---

### Example E — Not a kidney transplant specimen
> *"Final Diagnosis: Native kidney biopsy — focal segmental glomerulosclerosis."*

```json
{
  "transplant_confirmed": false,
  "transplant_type": "none",
  "donor_type": "none",
  "evidence_of_rejection": false,
  "rejection_type": "none",
  "evidence_of_graft_failure": false,
  "graft_failure_type": "none",
  "evidence_of_graft_complication": false,
  "complication_type": "none",
  "certainty": "none",
  "temporal_status": "unclear",
  "human_review": false,
  "human_review_reason": null
}
```

---

### Example F — Multiple complications, clean labels only
> *Path report with active AMR, severe chronic nephropathy, IgA deposits, foot process effacement, and history of FSGS.*

```json
{
  "transplant_confirmed": true,
  "transplant_type": "kidney",
  "donor_type": "deceased",
  "evidence_of_rejection": true,
  "rejection_type": "active antibody-mediated rejection",
  "evidence_of_graft_failure": false,
  "graft_failure_type": "none",
  "evidence_of_graft_complication": true,
  "complication_type": "chronic allograft nephropathy | IgA nephropathy | foot process effacement | recurrent FSGS",
  "certainty": "definite",
  "temporal_status": "mixed",
  "human_review": false,
  "human_review_reason": null
}
```
---

## FINAL SAFETY NET
After all steps, ask:
> *"If this patient had active rejection and this path report was the only document available, would I have caught it?"*
- **NO** → increase certainty one level + set `human_review: true`
- **YES** → finalize as classified

"""

# ── Docs section ──────────────────────────────────────────────────────────────
def render_docs():
    with st.expander("📖  Documentation — How to use this app & JSON Schema Reference", expanded=False):
        st.markdown("## How to Use This App")
        st.markdown("""
This application uses a selected model to extract structured clinical information from **kidney transplant pathology reports**.

**Steps:**
1. *(Optional)* Edit the **System Prompt** in the sidebar to customise the model's behaviour.
2. Paste or type a **pathology report** (or any clinical text) into the *User Prompt* box.
3. Click **Analyse Report**.
4. The structured output is displayed as clearly labelled cards — no raw JSON required.

**Tips:**
- The system prompt is pre-filled with the optimal instruction set for this schema. Only change it if you know what you're doing.
- Longer, more detailed reports yield more accurate extractions.
- If `Human Review Required` is flagged, a clinician should verify the result before downstream use.
        """)

        st.divider()
        st.markdown("## JSON Output Schema — 13 Keys")

        schema_rows = [
            ("transplant_confirmed", "Boolean", "`true` — kidney allograft confirmed | `false` — not a transplant specimen"),
            ("transplant_type", "String", "`\"kidney\"` | `\"none\"`"),
            ("donor_type", "String", "`\"deceased\"` | `\"living\"` | `\"unknown\"` | `\"none\"`"),
            ("evidence_of_rejection", "Boolean", "`true` — rejection present/suspected/possible | `false` — no evidence"),
            ("rejection_type", "String", "Short label (max 5 words). Multiple types separated by ` | `. `\"none\"` if absent."),
            ("evidence_of_graft_failure", "Boolean", "`true` — graft failure documented or implied | `false` — no evidence"),
            ("graft_failure_type", "String", "Short label (max 5 words). `\"none\"` if absent."),
            ("evidence_of_graft_complication", "Boolean", "`true` — any complication other than rejection | `false` — none"),
            ("complication_type", "String", "Short label(s). Multiple separated by ` | `. `\"none\"` if absent."),
            ("certainty", "String", "`\"definite\"` | `\"probable\"` | `\"possible\"` | `\"none\"`"),
            ("temporal_status", "String", "`\"acute/current\"` | `\"chronic/ongoing\"` | `\"historical/resolved\"` | `\"mixed\"` | `\"unclear\"`"),
            ("human_review", "Boolean", "`true` — ambiguity requires clinician check | `false` — clear classification"),
            ("human_review_reason", "String / null", "One sentence reason when `human_review` is `true`; `null` otherwise"),
        ]

        col_k, col_t, col_d = st.columns([2, 1.2, 4])
        col_k.markdown("**Key**")
        col_t.markdown("**Type**")
        col_d.markdown("**Values / Meaning**")
        st.markdown("---")

        for key, typ, desc in schema_rows:
            c1, c2, c3 = st.columns([2, 1.2, 4])
            c1.code(key)
            c2.write(typ)
            c3.markdown(desc)

# ── Sidebar — System prompt ───────────────────────────────────────────────────
with st.sidebar:
    st.image(
        "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3b/Antu_urology.svg/240px-Antu_urology.svg.png",
        width=60
    )
    st.title("⚙️ Configuration")
    st.markdown("**System Prompt**")
    system_prompt = st.text_area(
        label="system_prompt",
        value=DEFAULT_SYSTEM,
        height=420,
        label_visibility="collapsed",
        help="Instructions sent to the model before the user report."
    )
    st.caption("Modify only if you need to adjust model behaviour.")

    st.divider()
    st.markdown("**🤖 Model Selection**")
    selected_model = st.selectbox(
        label="model_selector",
        options=NAVIGATOR_MODELS,
        index=NAVIGATOR_MODELS.index(DEFAULT_MODEL),
        label_visibility="collapsed",
        help="Select the model to run analysis with.",
    )
    if selected_model in FRONTIER_MODELS:
        st.caption("⚡ Frontier model — requires elevated API access.")
    else:
        st.caption("✅ Open-source model — generally available.")

# ── Main area ─────────────────────────────────────────────────────────────────
st.title("🫘 Kidney Transplant NLP Analyser")
st.caption("Paste a pathology report below and click **Analyse Report** to extract structured clinical data.")

render_docs()

user_prompt = st.text_area(
    "📋 Pathology Report / User Prompt",
    placeholder="Paste the full pathology report text here…",
    height=220,
)

col_btn, col_model = st.columns([3, 2])
with col_btn:
    analyse_btn = st.button("🔬 Analyse Report", type="primary", use_container_width=True)
with col_model:
    st.markdown(
        f'<div style="padding:8px 0;color:#64748b;font-size:0.85rem;">🤖 Running with <strong>{selected_model}</strong></div>',
        unsafe_allow_html=True,
    )

# ── Helper renderers ──────────────────────────────────────────────────────────
def bool_badge(val: bool) -> str:
    cls = "badge-true" if val else "badge-false"
    label = "✓ Yes" if val else "✗ No"
    return f'<span class="badge {cls}">{label}</span>'

def certainty_badge(val: str) -> str:
    cls = f"badge-{val}" if val in ("definite", "probable", "possible", "none") else "badge-none"
    return f'<span class="badge {cls}">{val.capitalize()}</span>'

def field(label: str, value) -> str:
    return f'<div class="field-label">{label}</div><div class="field-value">{value}</div>'

def normalize_output(data: dict) -> dict:
    expected = {
        "transplant_confirmed": False,
        "transplant_type": "none",
        "donor_type": "none",
        "evidence_of_rejection": False,
        "rejection_type": "none",
        "evidence_of_graft_failure": False,
        "graft_failure_type": "none",
        "evidence_of_graft_complication": False,
        "complication_type": "none",
        "certainty": "none",
        "temporal_status": "unclear",
        "human_review": False,
        "human_review_reason": None,
    }

    if not isinstance(data, dict):
        return expected

    normalized = expected.copy()
    for key in expected:
        if key in data:
            normalized[key] = data[key]

    return normalized

def render_result(data: dict):
    if data.get("human_review"):
        reason = data.get("human_review_reason") or "Manual review required."
        st.markdown(f"""
        <div class="review-banner">
            <span style="font-size:1.4rem">⚠️</span>
            <div>
                <strong style="color:#92400e;">Human Review Required</strong><br>
                <span style="color:#78350f;">{reason}</span>
            </div>
        </div>""", unsafe_allow_html=True)

    st.markdown('<div class="section-header">Transplant Identity</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        color = "green" if data.get("transplant_confirmed") else "red"
        st.markdown(f"""
        <div class="result-card {color}">
            {field("Transplant Confirmed", bool_badge(data.get("transplant_confirmed", False)))}
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="result-card">
            {field("Transplant Type", str(data.get("transplant_type", "—")).upper())}
        </div>""", unsafe_allow_html=True)
    with c3:
        donor = str(data.get("donor_type", "—"))
        st.markdown(f"""
        <div class="result-card">
            {field("Donor Type", donor.capitalize())}
        </div>""", unsafe_allow_html=True)

    st.markdown('<div class="section-header">Rejection</div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    rej = data.get("evidence_of_rejection", False)
    with c1:
        color = "red" if rej else "green"
        st.markdown(f"""
        <div class="result-card {color}">
            {field("Evidence of Rejection", bool_badge(rej))}
        </div>""", unsafe_allow_html=True)
    with c2:
        rt = str(data.get("rejection_type", "none"))
        display = " &nbsp;|&nbsp; ".join([f"<code>{t.strip()}</code>" for t in rt.split("|")]) if rt != "none" else "—"
        st.markdown(f"""
        <div class="result-card">
            {field("Rejection Type", display)}
        </div>""", unsafe_allow_html=True)

    st.markdown('<div class="section-header">Graft Failure</div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    gf = data.get("evidence_of_graft_failure", False)
    with c1:
        color = "red" if gf else "green"
        st.markdown(f"""
        <div class="result-card {color}">
            {field("Evidence of Graft Failure", bool_badge(gf))}
        </div>""", unsafe_allow_html=True)
    with c2:
        gft = str(data.get("graft_failure_type", "none"))
        display = " &nbsp;|&nbsp; ".join([f"<code>{t.strip()}</code>" for t in gft.split("|")]) if gft != "none" else "—"
        st.markdown(f"""
        <div class="result-card">
            {field("Graft Failure Type", display)}
        </div>""", unsafe_allow_html=True)

    st.markdown('<div class="section-header">Graft Complications</div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    gc = data.get("evidence_of_graft_complication", False)
    with c1:
        color = "yellow" if gc else "green"
        st.markdown(f"""
        <div class="result-card {color}">
            {field("Evidence of Complication", bool_badge(gc))}
        </div>""", unsafe_allow_html=True)
    with c2:
        ct = str(data.get("complication_type", "none"))
        display = " &nbsp;|&nbsp; ".join([f"<code>{t.strip()}</code>" for t in ct.split("|")]) if ct != "none" else "—"
        st.markdown(f"""
        <div class="result-card">
            {field("Complication Type(s)", display)}
        </div>""", unsafe_allow_html=True)

    st.markdown('<div class="section-header">Classification Metadata</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f"""
        <div class="result-card">
            {field("Certainty", certainty_badge(str(data.get("certainty", "none"))))}
        </div>""", unsafe_allow_html=True)
    with c2:
        ts = str(data.get("temporal_status", "unclear"))
        ts_icons = {
            "acute/current": "🔴",
            "chronic/ongoing": "🟡",
            "historical/resolved": "🟢",
            "mixed": "🟠",
            "unclear": "⚪",
        }
        icon = ts_icons.get(ts, "⚪")
        st.markdown(f"""
        <div class="result-card">
            {field("Temporal Status", f"{icon} {ts.replace('/', ' / ').title()}")}
        </div>""", unsafe_allow_html=True)
    with c3:
        hr = data.get("human_review", False)
        color = "yellow" if hr else "green"
        st.markdown(f"""
        <div class="result-card {color}">
            {field("Human Review", bool_badge(hr))}
        </div>""", unsafe_allow_html=True)

    with st.expander("🔧 View raw JSON"):
        st.json(data)

# ── Run analysis ──────────────────────────────────────────────────────────────
if analyse_btn:
    if not user_prompt.strip():
        st.warning("Please enter a pathology report before clicking Analyse.")
    else:
        if not API_KEY:
            st.error("Missing NAVIGATOR_API_KEY. Add it to Streamlit Secrets or your local .env file.")
            st.stop()

        with st.spinner(f"Analysing report with **{selected_model}**…"):
            raw_text = ""
            try:
                client = OpenAI(
                    api_key=API_KEY,
                    base_url=BASE_URL,
                )

                response = client.chat.completions.create(
                    model=selected_model,
                    #max_tokens=1000,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )

                raw_text = response.choices[0].message.content.strip()

                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)

                data = json.loads(raw_text)
                data = normalize_output(data)

                st.success("Analysis complete.")
                st.markdown("---")
                render_result(data)

            except json.JSONDecodeError as e:
                st.error(f"Model returned invalid JSON: {e}")
                if raw_text:
                    st.code(raw_text, language="text")
            except Exception as e:
                st.error(f"An error occurred: {e}")