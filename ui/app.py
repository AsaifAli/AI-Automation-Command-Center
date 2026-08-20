import os
import time
from datetime import datetime

import requests
import streamlit as st

from sidebar_toggle import render_sidebar_toggle
from ui_theme import apply_theme

API = os.getenv("API_BASE_URL", "").strip()
if not API:
    api_host = os.getenv("API_HOST", "localhost").strip()
    api_port = os.getenv("API_PORT", "8000").strip()
    API = f"http://{api_host}:{api_port}"
API_KEY = os.getenv("API_KEY", "")
HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}

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
        "title": "Reachout Agent",
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


def request_headers() -> dict[str, str]:
    """Build request headers, including a portfolio-issued gateway session when present."""
    headers = dict(HEADERS)
    try:
        gateway_token = st.query_params.get("portfolio_llm_session", "")
    except Exception:
        gateway_token = ""
    gateway_token = str(gateway_token or "").strip()
    if gateway_token:
        headers["X-LLM-Gateway-Token"] = gateway_token
    return headers


st.set_page_config(
    page_title="AI Automation Command Center",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

apply_theme()
render_sidebar_toggle()


def api_get(path: str):
    return requests.get(f"{API}{path}", headers=request_headers(), timeout=8)


def api_post(path: str, body: dict):
    return requests.post(
        f"{API}{path}",
        headers={**request_headers(), "Content-Type": "application/json"},
        json=body,
        timeout=15,
    )


def api_delete(path: str):
    return requests.delete(f"{API}{path}", headers=request_headers(), timeout=15)


def response_json(response: requests.Response, context: str):
    """Safely decode an API response and expose useful diagnostics in the UI."""
    if not response.ok:
        detail = response.text.strip() or "empty response body"
        raise RuntimeError(f"{context} failed ({response.status_code}): {detail[:500]}")
    try:
        return response.json()
    except ValueError as exc:
        detail = response.text.strip() or "empty response body"
        raise RuntimeError(
            f"{context} returned a non-JSON response ({response.status_code}): {detail[:500]}"
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
except Exception as exc:
    st.error(f"API unavailable: {exc}")
    st.stop()

mode_label = "Demo" if health["demo_mode"] else "LLM"

st.markdown(
    f"""
    <section class="premium-hero">
      <div class="hero-topline">
        <div class="premium-kicker">FLOWPILOT · AI OPERATIONS</div>
        <span class="environment-badge"><span class="status-dot"></span>{'DEMO ENVIRONMENT' if health['demo_mode'] else 'LLM ENVIRONMENT'}</span>
      </div>
      <div class="premium-title">⚡ AI Automation Command Center</div>
      <div class="premium-subtitle">A portfolio-grade control plane for async AI workflows — with evidence, human approval, auditability, and operational visibility built into the experience.</div>
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
    f'<div class="sidebar-health"><div><span class="status-dot"></span>API Online</div><small>Version · {health["version"]}</small><small>Queue · Redis + worker</small></div>',
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
        topics = st.text_input("Topics", "AI agents, DeFi infrastructure")
        payload["topics"] = [x.strip() for x in topics.split(",") if x.strip()]
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
    queue_clicked = st.button("▶  Queue automation", type="primary", use_container_width=True)

with right:
    workflow_preview(meta, mode_label)

if queue_clicked:
    try:
        response = api_post("/api/v1/runs", {"workflow": workflow, "payload": payload})
        queued = response_json(response, "Queue automation")
        run_id = queued["run_id"]
        st.session_state["active_run_id"] = run_id
        st.session_state["result"] = None
        st.session_state["poll_count"] = 0
        st.rerun()
    except Exception as exc:
        st.error(str(exc))

active_run_id = st.session_state.get("active_run_id")
if active_run_id:
    st.divider()
    st.markdown('<div class="section-kicker">LIVE EXECUTION</div>', unsafe_allow_html=True)
    try:
        result = fetch_run(active_run_id)
    except Exception as exc:
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
            f'<div class="live-card"><div class="live-card-top"><div><div class="ops-label">CURRENT EXECUTION</div><div class="live-title">{meta["icon"]} {meta["title"]}</div></div><span class="run-id">{active_run_id[:12]}</span></div><div class="live-status">',
            unsafe_allow_html=True,
        )
        render_status_pill(status)
        st.markdown("</div></div>", unsafe_allow_html=True)
    with detail_col:
        usage = result.get("usage") or {}
        st.markdown(
            f"""
            <div class="ops-grid">
              <div class="ops-panel"><div class="ops-label">LATENCY</div><div class="ops-value">{result.get('duration_ms', 0)} ms</div></div>
              <div class="ops-panel"><div class="ops-label">EST. COST</div><div class="ops-value">${usage.get('estimated_cost_usd', 0):.6f}</div></div>
            </div>
            """,
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
    c4.metric("Est. cost", f"${usage.get('estimated_cost_usd', 0):.6f}")
    tab1, tab2, tab3 = st.tabs(["Output", "Evidence", "Operations"])
    with tab1:
        st.json(result["output"])
    with tab2:
        evidence = result.get("evidence", [])
        if not evidence:
            st.caption("No evidence items were attached to this execution.")
        for item in evidence:
            st.info(f"**{item['title']}** — {item['detail']} · confidence {item['confidence']:.0%}")
        for warning in result.get("warnings", []):
            st.warning(warning)
    with tab3:
        st.code(result["run_id"])
        if workflow == "outreach" and result["status"] != "failed":
            reviewer = st.text_input("Reviewer", "Portfolio Reviewer", key="reviewer")
            note = st.text_input("Approval note", "Reviewed for demo", key="approval_note")
            a, b = st.columns(2)
            if a.button("Approve", use_container_width=True):
                response = api_post(
                    f"/api/v1/runs/{result['run_id']}/approval",
                    {"decision": "approved", "reviewer": reviewer, "note": note},
                )
                st.success("Approval recorded in the audit trail.") if response.ok else st.error(response.text)
            if b.button("Reject", use_container_width=True):
                response = api_post(
                    f"/api/v1/runs/{result['run_id']}/approval",
                    {"decision": "rejected", "reviewer": reviewer, "note": note},
                )
                st.warning("Rejection recorded in the audit trail.") if response.ok else st.error(response.text)

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

st.divider()
header_col, action_col = st.columns([7, 1.6], vertical_alignment="center")
with header_col:
    st.markdown('<div class="section-kicker">AUDIT TRAIL</div>', unsafe_allow_html=True)
    st.markdown('<div class="subsection-title">Recent executions</div>', unsafe_allow_html=True)
with action_col:
    if st.button("Clear history", use_container_width=True, help="Permanently remove completed and failed run history."):
        st.session_state["confirm_clear_history"] = True

if st.session_state.get("confirm_clear_history"):
    st.warning("This permanently removes completed and failed runs, including their audit events and approvals. Queued/running runs are protected.")
    confirm_col, cancel_col = st.columns([1, 1])
    with confirm_col:
        if st.button("Confirm clear", type="primary", use_container_width=True):
            try:
                response = api_delete("/api/v1/runs/history")
                cleared = response_json(response, "Clear run history")
                st.session_state["confirm_clear_history"] = False
                st.session_state["active_run_id"] = None
                st.session_state["result"] = None
                st.session_state["poll_count"] = 0
                st.success(f"Cleared {cleared.get('deleted_runs', 0)} run(s) from history.")
                st.rerun()
            except Exception as exc:
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
            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
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
            st.code(run["run_id"])
            st.json(run["output"])
except Exception as exc:
    st.caption(f"Run history unavailable: {exc}")

st.markdown(
    '<div class="premium-muted footer-note">Observability · Prometheus → Grafana &nbsp;•&nbsp; Traces · OpenTelemetry → Jaeger</div>',
    unsafe_allow_html=True,
)
