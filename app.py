from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from src.agents import ACTIONS
from src.llm import api_keys, model_name
from src.solution import handle_request_payload

ROOT = Path(__file__).resolve().parent
REQUESTS = json.loads((ROOT / "data" / "requests.json").read_text(encoding="utf-8"))
BY_ID = {r["request_id"]: r for r in REQUESTS}
REVIEW_LOG = ROOT / "outputs" / "human_reviews.jsonl"
ACTION_STYLE = {"PROCEED": st.success, "REVIEW": st.warning, "BUDGET_EXCEPTION": st.warning,
                "CLARIFY": st.info, "REUSE_EXISTING": st.info}

st.set_page_config(page_title="Procurement Request Copilot", layout="wide")
st.title("AI Procurement Request Copilot")
st.caption("Gathers evidence and recommends the next action. It never approves, purchases or changes budgets.")

# ------------------------------------------------------------------ sidebar: pick or write a request
with st.sidebar:
    architecture = st.radio("Architecture", ["single", "staged"], horizontal=True,
                            help="single = one agent with tools; staged = analyst agent -> policy/risk reviewer")
    st.caption(f"Model: `{model_name()}` | Gemini keys loaded: {len(api_keys())}"
               + ("" if api_keys() else " (deterministic rules only)"))
    mode = st.radio("Request source", ["Existing request", "New request"], horizontal=True)
    if mode == "Existing request":
        rid = st.selectbox("Request", list(BY_ID), format_func=lambda r: f"{r} - {BY_ID[r]['product_name']}")
        request = BY_ID[rid]
    else:
        with st.form("new_request"):
            request = {
                "request_id": st.text_input("Request ID", "REQ-NEW"),
                "requester_id": st.text_input("Requester employee ID", "E001"),
                "product_name": st.text_input("Product"),
                "vendor_name": st.text_input("Vendor"),
                "category": st.text_input("Category"),
                "annual_cost_usd": st.number_input("Annual cost (USD, 0 = unknown)", min_value=0.0, step=100.0) or None,
                "user_count": st.number_input("Users / licenses (0 = unknown)", min_value=0, step=1) or None,
                "business_justification": st.text_area("Business justification"),
                "data_access_level": st.selectbox("Data access level", ["unknown", "none", "internal_documents", "internal_marketing",
                                                                        "confidential_documents", "employee_pii", "customer_pii",
                                                                        "source_code", "production_telemetry", "credentials"]),
                "requested_integrations": [s.strip() for s in st.text_input("Integrations (comma separated)").split(",") if s.strip()],
                "urgency": st.selectbox("Urgency", ["normal", "high", "urgent"]),
            }
            st.form_submit_button("Use this request")

# ------------------------------------------------------------------ main panel
left, right = st.columns([0.9, 1.1], gap="large")
with left:
    st.subheader("Request details")
    st.caption("Untrusted business data - shown as submitted")
    st.table(pd.DataFrame({"field": list(request), "value": [json.dumps(v) if isinstance(v, list) else str(v) for v in request.values()]}))
    if st.button("Analyse request", type="primary", width="stretch"):
        with st.spinner(f"Running {architecture} architecture..."):
            st.session_state["result"] = handle_request_payload(request, architecture)

with right:
    result = st.session_state.get("result")
    if not result or result[0].request_id != request.get("request_id"):
        st.info("Run the analysis to see the recommendation and evidence.")
    else:
        decision, trace = result
        st.subheader("Recommendation")
        ACTION_STYLE[trace["action"]](decision.recommendation)
        st.markdown(f"**Next step:** {decision.next_step}")
        c1, c2, c3 = st.columns(3)
        c1.markdown("**Approvals required**\n\n" + ("\n".join(f"- {a}" for a in decision.required_approvals) or "-"))
        c2.markdown("**Risk flags**\n\n" + ("\n".join(f"- `{f}`" for f in decision.risk_flags) or "none"))
        c3.markdown("**Missing information**\n\n" + ("\n".join(f"- {m}" for m in decision.missing_information) or "none"))

        st.subheader("Evidence")
        st.dataframe(pd.DataFrame([e.model_dump() for e in decision.evidence]), hide_index=True, width="stretch")

        tel = decision.telemetry
        st.caption(f"{trace['architecture']} | {trace['latency_ms']:.0f} ms | LLM calls {tel.llm_calls} | tool calls {tel.tool_calls}"
                   + (f" | guard corrections {len(trace['guard_corrections'])}" if trace["guard_corrections"] else ""))
        with st.expander("Agent trace (LLM output, guard corrections, tools)"):
            st.json({k: v for k, v in trace.items()})

        st.subheader("Human review")
        with st.form("review"):
            reviewer = st.text_input("Reviewer name")
            verdict = st.radio("Decision", ["Send to approvers as recommended", "Request more information", "Reject request",
                                            "Override recommendation (exception)"])
            comment = st.text_area("Comment / exception reason")
            if st.form_submit_button("Record human decision"):
                if not reviewer.strip():
                    st.error("Reviewer name is required.")
                else:
                    REVIEW_LOG.parent.mkdir(exist_ok=True)
                    with REVIEW_LOG.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "request_id": decision.request_id,
                                            "copilot_action": trace["action"], "reviewer": reviewer, "decision": verdict,
                                            "comment": comment}) + "\n")
                    st.success(f"Recorded. Logged to {REVIEW_LOG.relative_to(ROOT)}; no purchase or approval was made by the copilot.")

st.divider()
st.caption("Actions: " + " | ".join(ACTIONS.values()))
