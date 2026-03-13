import streamlit as st
from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()
import json
import re

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

# ── Default system prompt ─────────────────────────────────────────────────────
DEFAULT_SYSTEM = """You are a clinical NLP model specialized in kidney transplant pathology. 
Your task is to analyze pathology reports and extract structured information according to a strict JSON schema.

You MUST return ONLY a valid JSON object — no markdown, no explanation, no preamble. 
The JSON must contain exactly these 13 keys:

{
  "transplant_confirmed": true | false,
  "transplant_type": "kidney" | "none",
  "donor_type": "deceased" | "living" | "unknown" | "none",
  "evidence_of_rejection": true | false,
  "rejection_type": "<short label or 'none'>",
  "evidence_of_graft_failure": true | false,
  "graft_failure_type": "<short label or 'none'>",
  "evidence_of_graft_complication": true | false,
  "complication_type": "<short label, use ' | ' for multiple, or 'none'>",
  "certainty": "definite" | "probable" | "possible" | "none",
  "temporal_status": "acute/current" | "chronic/ongoing" | "historical/resolved" | "mixed" | "unclear",
  "human_review": true | false,
  "human_review_reason": "<one sentence reason>" | null
}

Rules:
- rejection_type, graft_failure_type, complication_type: max 5 words per type; separate multiple with ' | '
- human_review is true when: certainty is "possible", equivocal language used, findings present but no label, or conflicting information
- human_review_reason: one concise sentence when human_review is true; null otherwise
- Default all fields when transplant_confirmed is false
- Return ONLY the JSON object. Nothing else."""


# ── Docs section ──────────────────────────────────────────────────────────────
def render_docs():
    with st.expander("📖  Documentation — How to use this app & JSON Schema Reference", expanded=False):
        st.markdown("## How to Use This App")
        st.markdown("""
This application uses Claude (Anthropic) to extract structured clinical information from **kidney transplant pathology reports**.

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
        col_k.markdown("**Key**"); col_t.markdown("**Type**"); col_d.markdown("**Values / Meaning**")
        st.markdown("---")
        for key, typ, desc in schema_rows:
            c1, c2, c3 = st.columns([2, 1.2, 4])
            c1.code(key)
            c2.write(typ)
            c3.markdown(desc)


# ── Sidebar — System prompt ───────────────────────────────────────────────────
with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/3/3b/Antu_urology.svg/240px-Antu_urology.svg.png", width=60)
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
        index=NAVIGATOR_MODELS.index("claude-4.5-sonnet"),
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
    cls = f"badge-{val}" if val in ("definite","probable","possible","none") else "badge-none"
    return f'<span class="badge {cls}">{val.capitalize()}</span>'

def field(label: str, value) -> str:
    return f'<div class="field-label">{label}</div><div class="field-value">{value}</div>'

def render_result(data: dict):
    # Human review banner
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

    # ── Row 1: Transplant identity ─────────────────────────────────────────
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
            {field("Transplant Type", data.get("transplant_type","—").upper())}
        </div>""", unsafe_allow_html=True)
    with c3:
        donor = data.get("donor_type","—")
        st.markdown(f"""
        <div class="result-card">
            {field("Donor Type", donor.capitalize())}
        </div>""", unsafe_allow_html=True)

    # ── Row 2: Rejection ───────────────────────────────────────────────────
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
        rt = data.get("rejection_type","none")
        display = " &nbsp;|&nbsp; ".join([f"<code>{t.strip()}</code>" for t in rt.split("|")]) if rt != "none" else "—"
        st.markdown(f"""
        <div class="result-card">
            {field("Rejection Type", display)}
        </div>""", unsafe_allow_html=True)

    # ── Row 3: Graft failure ───────────────────────────────────────────────
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
        gft = data.get("graft_failure_type","none")
        display = " &nbsp;|&nbsp; ".join([f"<code>{t.strip()}</code>" for t in gft.split("|")]) if gft != "none" else "—"
        st.markdown(f"""
        <div class="result-card">
            {field("Graft Failure Type", display)}
        </div>""", unsafe_allow_html=True)

    # ── Row 4: Complications ───────────────────────────────────────────────
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
        ct = data.get("complication_type","none")
        display = " &nbsp;|&nbsp; ".join([f"<code>{t.strip()}</code>" for t in ct.split("|")]) if ct != "none" else "—"
        st.markdown(f"""
        <div class="result-card">
            {field("Complication Type(s)", display)}
        </div>""", unsafe_allow_html=True)

    # ── Row 5: Classification metadata ────────────────────────────────────
    st.markdown('<div class="section-header">Classification Metadata</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f"""
        <div class="result-card">
            {field("Certainty", certainty_badge(data.get("certainty","none")))}
        </div>""", unsafe_allow_html=True)
    with c2:
        ts = data.get("temporal_status","unclear")
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

    # ── Raw JSON toggle ────────────────────────────────────────────────────
    with st.expander("🔧 View raw JSON"):
        st.json(data)


# ── Run analysis ──────────────────────────────────────────────────────────────
if analyse_btn:
    if not user_prompt.strip():
        st.warning("Please enter a pathology report before clicking Analyse.")
    else:
        with st.spinner(f"Analysing report with **{selected_model}**…"):
            try:
                client = OpenAI(
                    api_key=os.getenv("NAVIGATOR_API_KEY"),
                    base_url="https://api.ai.it.ufl.edu",
                )
                response = client.chat.completions.create(
                    model=selected_model,
                    max_tokens=1000,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                raw_text = response.choices[0].message.content.strip()

                # Strip markdown code fences if present
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)

                data = json.loads(raw_text)
                st.success("Analysis complete.")
                st.markdown("---")
                render_result(data)

            except json.JSONDecodeError as e:
                st.error(f"Model returned invalid JSON: {e}")
                st.code(raw_text, language="text")
            except Exception as e:
                st.error(f"An error occurred: {e}")