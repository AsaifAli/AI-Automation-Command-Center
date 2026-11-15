import os
import time
from datetime import UTC, datetime
from html import escape

import requests
import streamlit as st
from api_access import build_api_headers
from content_input import parse_topic_briefs
from gateway_handoff import GatewayHandoffError, exchange_handoff_code
from output_formatting import build_client_report_markdown
from output_renderer import render_workflow_output
from sidebar_toggle import render_sidebar_toggle
from streamlit.errors import StreamlitAPIException
from ui_theme import apply_theme

API = os.getenv("API_BASE_URL", "").strip()
if not API:
    api_host = os.getenv("API_HOST", "localhost").strip()
    api_port = os.getenv("API_PORT", "8000").strip()
    API = f"http://{api_host}:{api_port}"

# UI credentials are role-scoped. API_KEY remains a local backwards-compatible
# fallback for deployments that have not yet provisioned separate role keys.
LEGACY_API_KEY = os.getenv("API_KEY", "").strip()
API_OPERATOR_KEY = os.getenv("API_OPERATOR_KEY", "").strip()
API_REVIEWER_KEY = os.getenv("API_REVIEWER_KEY", "").strip()
API_ADMIN_KEY = os.getenv("API_ADMIN_KEY", "").strip()
PORTFOLIO_SESSION_GATEWAY_URL = os.getenv("PORTFOLIO_SESSION_GATEWAY_URL", "").strip()

WORKFLOW_META = {
    "content": {
        "icon": "✍️",
        "title": "Content Agent",
        "purpose": "Generate channel-aware drafts. Publishing remains a human decision.",
        "output": "Multi-channel draft",
        "stages": ["Topic analysis", "Channel adaptation", "LLM generation", "Human review"],
    },
    "competitor": {
        "icon": "🔎",
        "title": "Competitor Intelligence",
        "purpose": "Ingest optional RSS/Atom sources and produce evidence-oriented intelligence.",
        "output": "Evidence-backed brief",
        "stages": ["Source ingestion", "Signal extraction", "Evidence synthesis", "Briefing"],
    },
    "outreach": {
        "icon": "🤝",
        "title": "Outreach Agent",
        "purpose": "Qualify opportunities and draft outreach. External sending requires approval.",
        "output": "Approval-ready draft",
        "stages": ["Qualification", "Context enrichment", "Draft generation", "Human approval"],
    },
    "kpi": {
        "icon": "📊",
        "title": "KPI & Progress Tracker",
        "purpose": "Normalize updates, surface blockers, and prepare a leadership briefing.",
        "output": "Leadership briefing",
        "stages": ["Normalize updates", "Detect blockers", "Summarize progress", "Briefing"],
    },
}


def _remove_query_parameter(name: str) -> str:
    """Read and immediately remove a sensitive handoff parameter from the URL."""
    try:
        value = st.query_params.get(name, "")
        if name in st.query_params:
            del st.query_params[name]
    except (AttributeError, KeyError, RuntimeError, StreamlitAPIException):
        return ""
    return str(value or "").strip()


def _clear_gateway_session() -> None:
    st.session_state.pop("llm_gateway_session_token", None)
    st.session_state.pop("llm_gateway_session_expires_at", None)
    st.session_state.pop("gateway_session_provider", None)


def _process_gateway_handoff() -> None:
    """Consume a one-time handoff; never accept a reusable token from the URL."""
    legacy_token = _remove_query_parameter("portfolio_llm_session")
    if legacy_token:
        _clear_gateway_session()
        st.session_state["gateway_handoff_error"] = (
            "This older session link is no longer supported. Reopen the demo from the portfolio."
        )
        return

    handoff_code = _remove_query_parameter("portfolio_llm_handoff")
    if not handoff_code:
        return

    _clear_gateway_session()
    try:
        exchanged = exchange_handoff_code(
            handoff_code,
            PORTFOLIO_SESSION_GATEWAY_URL,
        )
    except GatewayHandoffError as exc:
        st.session_state["gateway_handoff_error"] = str(exc)
        return

    st.session_state["llm_gateway_session_token"] = exchanged["access_token"]
    st.session_state["llm_gateway_session_expires_at"] = (
        datetime.now(UTC).timestamp() + exchanged["expires_in"]
    )
    st.session_state["gateway_session_provider"] = exchanged.get("provider", "")
    st.session_state.pop("gateway_handoff_error", None)


def _manual_reviewer_key() -> str:
    key = str(st.session_state.get("manual_reviewer_api_key", "")).strip()
    try:
        expires_at = float(st.session_state.get("manual_reviewer_expires_at", 0))
    except (TypeError, ValueError):
        expires_at = 0
    if not key or expires_at <= time.time():
        st.session_state.pop("manual_reviewer_api_key", None)
        st.session_state.pop("manual_reviewer_expires_at", None)
        return ""
    return key


def gateway_session_is_active() -> bool:
    """Return whether this Streamlit server session holds a non-expired Gateway bearer."""
    token = str(st.session_state.get("llm_gateway_session_token", "")).strip()
    try:
        expires_at = float(st.session_state.get("llm_gateway_session_expires_at", 0))
    except (TypeError, ValueError, OverflowError):
        return False
    return bool(token and expires_at > time.time())


def request_headers(role: str = "operator") -> dict[str, str]:
    """Build role-scoped API headers plus a non-expired server-side LLM session."""
    role_keys = {
        "operator": API_OPERATOR_KEY or LEGACY_API_KEY,
        "reviewer": (
            API_REVIEWER_KEY
            or _manual_reviewer_key()
            or LEGACY_API_KEY
        ),
        "admin": API_ADMIN_KEY or LEGACY_API_KEY,
    }
    api_key = role_keys.get(role, "")
    return build_api_headers(
        api_key,
        str(st.session_state.get("llm_gateway_session_token", "")),
        st.session_state.get("llm_gateway_session_expires_at"),
    )


st.set_page_config(
    page_title="AI Automation Command Center",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

_process_gateway_handoff()
apply_theme()
render_sidebar_toggle()
if st.session_state.get("gateway_handoff_error"):
    st.warning(st.session_state["gateway_handoff_error"])


def api_get(path: str, role: str = "operator"):
    return requests.get(f"{API}{path}", headers=request_headers(role), timeout=8)


def api_post(path: str, body: dict, role: str = "operator"):
    return requests.post(
        f"{API}{path}",
        headers={**request_headers(role), "Content-Type": "application/json"},
        json=body,
        timeout=15,
    )


def api_delete(path: str, role: str = "admin"):
    return requests.delete(f"{API}{path}", headers=request_headers(role), timeout=15)


def queue_workflow(workflow_name: str, payload: dict) -> str:
    response = api_post("/api/v1/runs", {"workflow": workflow_name, "payload": payload})
    queued = response_json(response, "Queue automation")
    return queued["run_id"]


def response_json(response: requests.Response, context: str):
    """Decode API responses without leaking internal server errors to the UI."""
    if not response.ok:
        detail = None
        try:
            body = response.json()
            detail = body.get("detail") if isinstance(body, dict) else None
        except ValueError:
            detail = None
        if response.status_code == 429:
            message = "The service is receiving too many requests. Please wait and retry."
        elif response.status_code < 500 and isinstance(detail, str) and detail.strip():
            message = detail.strip()[:240]
        elif response.status_code >= 500:
            message = "The service could not complete this request. Please retry or share the run reference with an administrator."
        else:
            message = "The request was not accepted. Check the submitted details and your access permissions."
        raise RuntimeError(f"{context} failed ({response.status_code}): {message}")
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"{context} returned an unexpected response. Please retry or contact an administrator."
        ) from exc


def fetch_run(run_id: str):
    response = api_get(f"/api/v1/runs/{run_id}")
    return response_json(response, f"Fetching run {run_id}")


def render_status_pill(status: str) -> None:
    normalized = (status or "unknown").lower()
    dot_class = "status-dot"
    if normalized in {"pending", "rejected"}:
        dot_class = "status-dot warn"
    elif normalized not in {"queued", "running", "completed", "completed_with_warnings", "approved"}:
        dot_class = "status-dot fail"
    label = normalized.replace("_", " ").upper()
    st.markdown(
        f'<span class="status-pill"><span class="{dot_class}"></span>{label}</span>',
        unsafe_allow_html=True,
    )


def workflow_preview(meta: dict, mode_label: str) -> None:
    stages = "".join(
        f'<div class="workflow-step"><span class="workflow-step-index">{index:02d}</span>'
        f'<span>{stage}</span></div>'
        for index, stage in enumerate(meta["stages"], 1)
    )
    st.markdown(
        f"""
        <div class="workflow-preview">
          <div class="preview-head">
            <div>
              <div class="ops-label">WORKFLOW PREVIEW</div>
              <div class="preview-title">{meta['icon']} {meta['title']}</div>
            </div>
            <span class="ready-badge"><span class="status-dot"></span>READY</span>
          </div>
          <div class="preview-output"><span>Expected output</span><strong>{meta['output']}</strong></div>
          <div class="workflow-steps">{stages}</div>
          <div class="preview-footer"><span>Execution mode</span><strong>{mode_label}</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


try:
    health = response_json(api_get("/health"), "Health check")
    metrics = response_json(api_get("/metrics"), "Metrics request")
except (requests.RequestException, RuntimeError, ValueError, KeyError) as exc:
    st.error(f"API unavailable: {exc}")
    st.stop()

gateway_active = gateway_session_is_active()
mode_label = (
    "BYOK Gateway"
    if gateway_active
    else "Demo"
    if health["demo_mode"]
    else "Service LLM"
)
environment_label = (
    "BYOK GATEWAY SESSION"
    if gateway_active
    else "DEMO ENVIRONMENT"
    if health["demo_mode"]
    else "SERVICE LLM"
)

st.markdown(
    f"""
    <section class="premium-hero">
      <div class="hero-topline">
        <div class="premium-kicker">FLOWPILOT · AI OPERATIONS</div>
        <span class="environment-badge"><span class="status-dot"></span>{environment_label}</span>
      </div>
      <div class="premium-title">⚡ AI Automation Command Center</div>
      <div class="premium-subtitle">An operations workspace for AI-powered business workflows — combining evidence, human approvals, audit trails, and operational visibility.</div>
      <div class="premium-strip">
        <span class="premium-chip"><span class="premium-dot"></span>Async workflows</span>
        <span class="premium-chip"><span class="premium-dot"></span>Evidence-aware</span>
        <span class="premium-chip"><span class="premium-dot"></span>Human approval</span>
        <span class="premium-chip"><span class="premium-dot"></span>Audit trail</span>
        <span class="premium-chip"><span class="premium-dot"></span>Observability</span>
      </div>
    </section>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="section-kicker">SYSTEM PULSE</div>', unsafe_allow_html=True)
pulse = [
    ("API", "Online", "Gateway reachable", "online"),
    ("MODE", mode_label, "Execution profile", "neutral"),
    ("RUNS", str(metrics["workflow_runs_total"]), "All-time executions", "neutral"),
    ("RUNNING", str(metrics["workflow_runs_running"]), "Active worker jobs", "neutral"),
    ("APPROVED", str(metrics["workflow_approvals_approved"]), "Human decisions", "neutral"),
]
pulse_cols = st.columns(5)
for column, (label, value, detail, tone) in zip(pulse_cols, pulse):
    with column:
        st.markdown(
            f"""
            <div class="pulse-card">
              <div class="pulse-label">{label}</div>
              <div class="pulse-value {tone}">{value}</div>
              <div class="pulse-detail">{detail}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.sidebar.markdown('<div class="sidebar-brand"><span class="sidebar-brand-mark">⚡</span><div><strong>FLOWPILOT</strong><small>AI OPERATIONS</small></div></div>', unsafe_allow_html=True)
st.sidebar.markdown('<div class="premium-kicker">WORKFLOWS</div>', unsafe_allow_html=True)
workflow = st.sidebar.radio(
    "Automation",
    list(WORKFLOW_META),
    format_func=lambda value: f"{WORKFLOW_META[value]['icon']} {WORKFLOW_META[value]['title']}",
    label_visibility="collapsed",
)
st.sidebar.divider()
st.sidebar.markdown('<div class="premium-kicker">SYSTEM HEALTH</div>', unsafe_allow_html=True)
st.sidebar.markdown(
    f'<div class="sidebar-health"><div><span class="status-dot"></span>API Online</div><small>Version · {escape(str(health["version"]))}</small><small>Queue · Redis + worker</small></div>',
    unsafe_allow_html=True,
)
st.sidebar.divider()
st.sidebar.markdown('<div class="sidebar-footer-note">Human-in-the-loop controls<br>keep external actions approval-gated.</div>', unsafe_allow_html=True)

meta = WORKFLOW_META[workflow]

st.markdown('<div class="section-kicker">AUTOMATION</div>', unsafe_allow_html=True)
left, right = st.columns([1.22, 0.78], gap="large")
payload = {}
with left:
    st.markdown(
        f'<div class="workflow-heading"><span class="workflow-icon">{meta["icon"]}</span><div><h2>{meta["title"]}</h2><p>{meta["purpose"]}</p></div></div>',
        unsafe_allow_html=True,
    )

    if workflow == "content":
        brief_text = st.text_area(
            "Content briefs",
            "AI agents in enterprise finance operations, including reconciliation and exception handling",
            height=120,
            max_chars=5000,
            help="Enter up to five complete briefs, one per line. Commas, punctuation, and full sentences are preserved; do not separate ideas with commas.",
            placeholder="One complete brief per line…",
        )
        topic_briefs = parse_topic_briefs(brief_text)
        if len(topic_briefs) > 5:
            st.warning("A single run supports up to five briefs. Remove the extra lines before queuing.")
            payload["topics"] = []
        else:
            payload["topics"] = topic_briefs
        payload["channels"] = st.multiselect(
            "Channels",
            ["linkedin", "x", "telegram"],
            ["linkedin", "x", "telegram"],
        )
        payload["tone"] = st.selectbox("Tone", ["insightful", "executive", "technical", "concise"])
    elif workflow == "competitor":
        names = st.text_input("Competitors", "Example Protocol, Example AI Startup")
        payload["competitors"] = [x.strip() for x in names.split(",") if x.strip()]
        source_url = st.text_input("Optional RSS/Atom source URL", "")
        if source_url:
            payload["sources"] = [{"name": "Runtime source", "url": source_url}]
    elif workflow == "outreach":
        name = st.text_input("Candidate name", "Demo Partner")
        context = st.text_input("Relationship context", "AI infrastructure collaboration")
        channel = st.selectbox("Preferred channel", ["email", "linkedin", "x", "telegram"])
        payload["candidates"] = [{"name": name, "type": "partner", "context": context, "channel": channel}]
    else:
        entity = st.text_input("Entity", "Demo Portfolio")
        metric = st.text_input("Metric", "weekly progress")
        value = st.text_input("Value", "on track")
        blocker = st.text_input("Blocker", "None reported")
        payload["updates"] = [{"entity": entity, "metric": metric, "value": value, "blocker": blocker}]

    st.markdown('<div class="config-note">Publishing and external actions remain human-controlled.</div>', unsafe_allow_html=True)
    action_col1, action_col2 = st.columns([1.45, 1], gap="small")
    with action_col1:
        queue_clicked = st.button("▶  Queue automation", type="primary", use_container_width=True)
    with action_col2:
        if health["demo_mode"] and not gateway_active:
            demo_clicked = st.button(
                "⚡ Run sample",
                use_container_width=True,
                help="Queue a safe sample workflow using demo-mode output.",
            )
        else:
            demo_clicked = False
            st.caption("Live model generation · provider usage may incur charges.")

with right:
    workflow_preview(meta, mode_label)

if queue_clicked or demo_clicked:
    try:
        run_payload = payload
        if demo_clicked:
            run_payload = {
                "content": {"topics": ["AI agents, DeFi infrastructure"], "channels": ["linkedin", "x", "telegram"], "tone": "insightful"},
                "competitor": {"competitors": ["Example Protocol", "Example AI Startup"]},
                "outreach": {"candidates": [{"name": "Demo Partner", "type": "partner", "context": "AI infrastructure collaboration", "channel": "linkedin"}]},
                "kpi": {"updates": [{"entity": "Demo Portfolio", "metric": "weekly progress", "value": "on track", "blocker": "None reported"}]},
            }[workflow]
        if workflow == "content" and not run_payload.get("topics"):
            st.warning("Add at least one complete content brief before queuing this workflow.")
        elif workflow == "content" and not run_payload.get("channels"):
            st.warning("Select at least one destination channel before queuing this workflow.")
        else:
            run_id = queue_workflow(workflow, run_payload)
            st.session_state["active_run_id"] = run_id
            st.session_state["result"] = None
            st.session_state["poll_count"] = 0
            st.session_state["demo_run"] = demo_clicked
            st.rerun()
    except (requests.RequestException, RuntimeError, ValueError, KeyError):
        st.error("FlowPilot could not queue this workflow. Check the service status and submitted details, then retry.")

active_run_id = st.session_state.get("active_run_id")
if active_run_id:
    st.divider()
    st.markdown('<div class="section-kicker">LIVE EXECUTION</div>', unsafe_allow_html=True)
    if st.session_state.get("demo_run"):
        st.markdown('<div class="demo-note">⚡ Portfolio demo run · using safe sample inputs</div>', unsafe_allow_html=True)
    try:
        result = fetch_run(active_run_id)
    except (requests.RequestException, RuntimeError, ValueError, KeyError) as exc:
        poll_count = st.session_state.get("poll_count", 0) + 1
        st.session_state["poll_count"] = poll_count
        st.warning(f"Waiting for the run to become available… {exc}")
        if poll_count >= 40:
            st.error("Unable to retrieve the run after multiple attempts. Check the API and worker logs.")
        else:
            time.sleep(1.5)
            st.rerun()
        st.stop()

    status = result.get("status")
    live_col, detail_col = st.columns([1.35, 1], gap="large")
    with live_col:
        st.markdown(
            f'<div class="live-card"><div class="live-card-top"><div><div class="ops-label">CURRENT EXECUTION</div><div class="live-title">{meta["icon"]} {escape(str(meta["title"]))}</div></div><span class="run-id">{escape(active_run_id[:12])}</span></div><div class="live-status">',
            unsafe_allow_html=True,
        )
        render_status_pill(status)
        st.markdown("</div></div>", unsafe_allow_html=True)
    with detail_col:
        usage = result.get("usage") or {}
        cost_available = usage.get("cost_estimate_available", True)
        cost_label = (
            f"${usage.get('estimated_cost_usd', 0):.6f}"
            if cost_available
            else "Billed by provider"
        )
        st.markdown(
            f"""
            <div class="ops-grid">
              <div class="ops-panel"><div class="ops-label">LATENCY</div><div class="ops-value">{result.get('duration_ms', 0)} ms</div></div>
              <div class="ops-panel"><div class="ops-label">COST</div><div class="ops-value">{cost_label}</div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    stage_state = "active" if status in {"queued", "running"} else ("complete" if status in {"completed", "completed_with_warnings", "approved"} else "failed")
    steps = []
    for index, stage in enumerate(meta["stages"]):
        if stage_state == "complete":
            marker, cls = "✓", "complete"
        elif stage_state == "failed":
            marker, cls = "!", "failed"
        elif index == 0:
            marker, cls = "●", "active"
        else:
            marker, cls = f"{index + 1:02d}", "pending"
        steps.append(f'<div class="live-flow-step {cls}"><span class="live-flow-marker">{marker}</span><span>{stage}</span></div>')
    st.markdown(
        f'<div class="live-flow"><div class="ops-label">EXECUTION PATH</div><div class="live-flow-steps">{"".join(steps)}</div><small>Stage-level telemetry is represented by the workflow lifecycle; detailed audit events remain available below.</small></div>',
        unsafe_allow_html=True,
    )

    if status in {"queued", "running"}:
        poll_count = st.session_state.get("poll_count", 0) + 1
        st.session_state["poll_count"] = poll_count
        st.progress(25 if status == "queued" else 65, text="Worker is processing the workflow…")
        if poll_count >= 40:
            st.warning("The run is taking longer than expected. It remains active in the worker queue; check Recent executions later.")
        else:
            time.sleep(1.5)
            st.rerun()
    else:
        st.session_state["result"] = result
        st.session_state["poll_count"] = 0

result = st.session_state.get("result")
if result:
    st.markdown('<div class="section-kicker">EXECUTION DETAILS</div>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status", result["status"].replace("_", " ").title())
    c2.metric("Latency", f"{result['duration_ms']} ms")
    c3.metric("Run ID", result["run_id"][:8])
    usage = result.get("usage") or {}
    c4.metric(
        "Est. cost",
        f"${usage.get('estimated_cost_usd', 0):.6f}"
        if usage.get("cost_estimate_available", True)
        else "Provider billed",
    )
    tab1, tab2, tab3 = st.tabs(["Client-ready output", "Evidence", "Operations"])
    result_workflow = str(result.get("workflow") or workflow)
    with tab1:
        output = result.get("output", {})
        report_markdown = build_client_report_markdown(
            result_workflow,
            output,
            result,
        )
        st.download_button(
            "Download client-ready report",
            data=report_markdown,
            file_name=f"flowpilot-{result_workflow}-{str(result.get('run_id', 'run'))[:8]}.md",
            mime="text/markdown",
            use_container_width=True,
            key=f"download_report_{result.get('run_id', 'latest')}",
        )
        render_workflow_output(result_workflow, output)
    with tab2:
        evidence = result.get("evidence", [])
        if not evidence:
            st.caption("No evidence items were attached to this execution.")
        for item in evidence:
            st.info(f"**{item['title']}** — {item['detail']} · confidence {item['confidence']:.0%}")
        for warning in result.get("warnings", []):
            st.warning(warning)
    with tab3:
        st.caption(f"Run reference · {result['run_id']}")
        usage = result.get("usage") or {}
        st.markdown("#### Generation details")
        generation_cols = st.columns(4)
        generation_cols[0].metric("Provider", str(usage.get("provider") or "Not recorded"))
        generation_cols[1].metric("Model", str(usage.get("model") or "Not recorded"))
        generation_cols[2].metric("Tokens", int(usage.get("total_tokens", 0) or 0))
        generation_cols[3].metric(
            "Cost estimate",
            f"${float(usage.get('estimated_cost_usd', 0) or 0):.6f}"
            if usage.get("cost_estimate_available", True)
            else "Provider billed",
        )
        st.caption("Token counts are provider-reported where available. BYOK provider charges are not estimated or billed by FlowPilot.")
        if result_workflow == "outreach" and result["status"] != "failed":
            effective_reviewer_key = (
                API_REVIEWER_KEY
                or _manual_reviewer_key()
                or LEGACY_API_KEY
            )
            if not effective_reviewer_key:
                with st.expander("Authorized reviewer access", expanded=False):
                    st.caption("Reviewer credentials are never preloaded into the public UI. Enter your assigned reviewer key to unlock approval controls for this browser session.")
                    reviewer_entry = st.text_input(
                        "Reviewer API key",
                        type="password",
                        key="reviewer_api_key_entry",
                    )
                    if st.button("Unlock reviewer controls", use_container_width=True):
                        if reviewer_entry.strip():
                            st.session_state["manual_reviewer_api_key"] = reviewer_entry.strip()
                            st.session_state["manual_reviewer_expires_at"] = time.time() + 900
                            st.rerun()
                        st.warning("Enter a reviewer key to continue.")
            else:
                note = st.text_area(
                    "Reviewer decision note",
                    "Reviewed for fit, accuracy and policy compliance.",
                    max_chars=1000,
                    key="approval_note",
                    height=80,
                )
                a, b = st.columns(2)
                if a.button("Approve draft", use_container_width=True, type="primary"):
                    response = api_post(
                        f"/api/v1/runs/{result['run_id']}/approval",
                        {"decision": "approved", "note": note},
                        role="reviewer",
                    )
                    if response.ok:
                        st.success("Approval recorded in the audit trail.")
                        st.rerun()
                    st.error("Approval could not be recorded. Check reviewer permissions and run status.")
                if b.button("Reject draft", use_container_width=True):
                    response = api_post(
                        f"/api/v1/runs/{result['run_id']}/approval",
                        {"decision": "rejected", "note": note},
                        role="reviewer",
                    )
                    if response.ok:
                        st.warning("Rejection recorded in the audit trail.")
                        st.rerun()
                    st.error("Rejection could not be recorded. Check reviewer permissions and run status.")
                if st.button("Disconnect reviewer", key="disconnect_reviewer"):
                    st.session_state.pop("manual_reviewer_api_key", None)
                    st.session_state.pop("manual_reviewer_expires_at", None)
                    st.rerun()

st.divider()
st.markdown('<div class="section-kicker">OPERATIONS</div>', unsafe_allow_html=True)
health_col, approval_col = st.columns([1.25, 0.75], gap="large")
with health_col:
    st.markdown('<div class="subsection-title">System health</div>', unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="health-grid">
          <div class="health-item"><span><i class="status-dot"></i>API</span><strong>Healthy</strong></div>
          <div class="health-item"><span><i class="status-dot"></i>Worker queue</span><strong>{metrics['workflow_runs_queued']} queued</strong></div>
          <div class="health-item"><span><i class="status-dot"></i>Workers</span><strong>{metrics['workflow_runs_running']} active</strong></div>
          <div class="health-item"><span><i class="status-dot"></i>Mode</span><strong>{mode_label}</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with approval_col:
    st.markdown('<div class="subsection-title">Approval center</div>', unsafe_allow_html=True)
    if metrics["workflow_approvals_total"]:
        st.markdown(
            f'<div class="approval-card"><div class="approval-number">{metrics["workflow_approvals_approved"]}</div><div><strong>approved decisions</strong><small>{metrics["workflow_approvals_total"]} total human decisions recorded</small></div></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="approval-card muted"><div class="approval-number">0</div><div><strong>No decisions yet</strong><small>Outreach approvals will appear here.</small></div></div>',
            unsafe_allow_html=True,
        )
    if not (API_REVIEWER_KEY or LEGACY_API_KEY or st.session_state.get("manual_reviewer_api_key")):
        st.caption("Reviewer actions are restricted. Authorized reviewers can unlock them inside an outreach run. Reviewer access expires after 15 minutes.")

st.divider()
header_col, action_col = st.columns([7, 1.6], vertical_alignment="center")
with header_col:
    st.markdown('<div class="section-kicker">AUDIT TRAIL</div>', unsafe_allow_html=True)
    st.markdown('<div class="subsection-title">Recent executions</div>', unsafe_allow_html=True)
with action_col:
    if (API_ADMIN_KEY or LEGACY_API_KEY) and st.button(
        "Clear history",
        use_container_width=True,
        help="Permanently remove completed and failed run history.",
    ):
        st.session_state["confirm_clear_history"] = True
    elif not (API_ADMIN_KEY or LEGACY_API_KEY):
        st.caption("Admin-only action")

if st.session_state.get("confirm_clear_history"):
    st.warning("This permanently removes completed and failed runs, including their audit events and approvals. Queued/running runs are protected.")
    confirm_col, cancel_col = st.columns([1, 1])
    with confirm_col:
        if st.button("Confirm clear", type="primary", use_container_width=True):
            try:
                response = api_delete("/api/v1/runs/history", role="admin")
                cleared = response_json(response, "Clear run history")
                st.session_state["confirm_clear_history"] = False
                st.session_state["active_run_id"] = None
                st.session_state["result"] = None
                st.session_state["poll_count"] = 0
                st.success(f"Cleared {cleared.get('deleted_runs', 0)} run(s) from history.")
                st.rerun()
            except (requests.RequestException, RuntimeError, ValueError, KeyError) as exc:
                st.error(str(exc))
    with cancel_col:
        if st.button("Cancel", use_container_width=True):
            st.session_state["confirm_clear_history"] = False
            st.rerun()

try:
    runs = response_json(api_get("/api/v1/runs?limit=10"), "Run history")["runs"]
    if not runs:
        st.markdown('<div class="empty-state"><strong>No executions yet</strong><span>Queue a workflow to populate the operational history.</span></div>', unsafe_allow_html=True)
    for run in runs:
        created = run["created_at"]
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        run_meta = WORKFLOW_META.get(run["workflow"], {"icon": "⚙️", "title": run["workflow"]})
        status_label = run["status"].replace("_", " ").upper()
        st.markdown(
            f"""
            <div class="execution-row">
              <div class="execution-icon">{run_meta['icon']}</div>
              <div class="execution-main"><strong>{run_meta['title']}</strong><span>{created.strftime('%Y-%m-%d %H:%M:%S UTC')} · {run['duration_ms']} ms</span></div>
              <div class="execution-status"><span class="status-pill">{status_label}</span><small>{run['run_id'][:8]}</small></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        with st.expander("View execution details", expanded=False):
            detail_a, detail_b, detail_c = st.columns(3)
            detail_a.markdown(f'<div class="detail-stat"><span>STATUS</span><strong>{status_label}</strong></div>', unsafe_allow_html=True)
            detail_b.markdown(f'<div class="detail-stat"><span>DURATION</span><strong>{run["duration_ms"]} ms</strong></div>', unsafe_allow_html=True)
            detail_c.markdown(f'<div class="detail-stat"><span>RUN ID</span><strong>{escape(str(run["run_id"])[:12])}</strong></div>', unsafe_allow_html=True)
            st.markdown('<div class="detail-label">CLIENT-READY RESULT</div>', unsafe_allow_html=True)
            render_workflow_output(run.get("workflow", ""), run.get("output", {}))
except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as exc:
    st.caption(f"Run history unavailable: {exc}")

st.markdown(
    '<div class="premium-muted footer-note">Observability · Prometheus → Grafana &nbsp;•&nbsp; Traces · OpenTelemetry → Jaeger</div>',
    unsafe_allow_html=True,
)
