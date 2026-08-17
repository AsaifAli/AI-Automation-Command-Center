import os
import time
from datetime import datetime

import requests
import streamlit as st

API = os.getenv("API_BASE_URL", "").strip()
if not API:
    api_host = os.getenv("API_HOST", "localhost").strip()
    api_port = os.getenv("API_PORT", "8000").strip()
    API = f"http://{api_host}:{api_port}"
API_KEY = os.getenv("API_KEY", "")
HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}


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

st.set_page_config(page_title="AI Automation Command Center", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
.hero {padding: .8rem 0 .3rem;}
.muted {opacity: .7;}
[data-testid="stMetricValue"] {font-size: 1.55rem;}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="hero">', unsafe_allow_html=True)
st.title("⚡ AI Automation Command Center")
st.markdown("**AI operations control plane** · async workflows · evidence · human approval · audit trail · observability")
st.markdown('<span class="muted">Portfolio-grade demonstration of production-minded AI automation architecture.</span>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)


def api_get(path: str):
    return requests.get(f"{API}{path}", headers=request_headers(), timeout=8)


def api_post(path: str, body: dict):
    return requests.post(f"{API}{path}", headers={**request_headers(), "Content-Type": "application/json"}, json=body, timeout=15)


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

try:
    health = response_json(api_get("/health"), "Health check")
    metrics = response_json(api_get("/metrics"), "Metrics request")
except Exception as exc:
    st.error(f"API unavailable: {exc}")
    st.stop()

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("API", "Online")
m2.metric("Mode", "Demo" if health["demo_mode"] else "LLM")
m3.metric("Runs", metrics["workflow_runs_total"])
m4.metric("Running", metrics["workflow_runs_running"])
m5.metric("Approved", metrics["workflow_approvals_approved"])

workflow = st.sidebar.radio("Automation", ["content", "competitor", "outreach", "kpi"], format_func=lambda x: {
    "content": "✍️ Content",
    "competitor": "🔎 Competitor Intel",
    "outreach": "🤝 Reachout",
    "kpi": "📊 KPI Briefing",
}[x])
st.sidebar.divider()
st.sidebar.caption(f"API: {API}")
st.sidebar.caption(f"Version: {health['version']}")
st.sidebar.caption("Execution: Redis queue + worker")

payload = {}
if workflow == "content":
    st.subheader("Content Agent")
    st.caption("Generate channel-aware drafts. Publishing remains a human decision.")
    topics = st.text_input("Topics", "AI agents, DeFi infrastructure")
    payload["topics"] = [x.strip() for x in topics.split(",") if x.strip()]
    payload["channels"] = st.multiselect("Channels", ["linkedin", "x", "telegram"], ["linkedin", "x", "telegram"])
    payload["tone"] = st.selectbox("Tone", ["insightful", "executive", "technical", "concise"])
elif workflow == "competitor":
    st.subheader("Competitor Intelligence Agent")
    st.caption("Optional RSS/Atom ingestion with evidence-oriented output.")
    names = st.text_input("Competitors", "Example Protocol, Example AI Startup")
    payload["competitors"] = [x.strip() for x in names.split(",") if x.strip()]
    source_url = st.text_input("Optional RSS/Atom source URL", "")
    if source_url:
        payload["sources"] = [{"name": "Runtime source", "url": source_url}]
elif workflow == "outreach":
    st.subheader("Reachout Agent")
    st.caption("Qualification and draft generation only. External sending is blocked until human approval.")
    name = st.text_input("Candidate name", "Demo Partner")
    context = st.text_input("Relationship context", "AI infrastructure collaboration")
    channel = st.selectbox("Preferred channel", ["email", "linkedin", "x", "telegram"])
    payload["candidates"] = [{"name": name, "type": "partner", "context": context, "channel": channel}]
else:
    st.subheader("KPI & Progress Tracker")
    st.caption("Normalize updates, surface blockers, and prepare a leadership briefing.")
    entity = st.text_input("Entity", "Demo Portfolio")
    metric = st.text_input("Metric", "weekly progress")
    value = st.text_input("Value", "on track")
    blocker = st.text_input("Blocker", "None reported")
    payload["updates"] = [{"entity": entity, "metric": metric, "value": value, "blocker": blocker}]

if st.button("▶ Queue automation", type="primary", use_container_width=True):
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
    st.subheader("Live execution")
    try:
        result = fetch_run(active_run_id)
    except Exception as exc:
        # A freshly queued job can briefly race API/DB readiness. Keep polling instead
        # of crashing the Streamlit page on an empty/non-JSON transient response.
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
    st.write(f"Run **{active_run_id}** · status **{status}**")
    if status in {"queued", "running"}:
        poll_count = st.session_state.get("poll_count", 0) + 1
        st.session_state["poll_count"] = poll_count
        st.progress(25 if status == "queued" else 65, text="Worker is processing the LangGraph workflow...")
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
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status", result["status"])
    c2.metric("Latency", f"{result['duration_ms']} ms")
    c3.metric("Run ID", result["run_id"][:8])
    usage = result.get("usage") or {}
    c4.metric("Est. cost", f"${usage.get('estimated_cost_usd', 0):.6f}")
    tab1, tab2, tab3 = st.tabs(["Output", "Evidence", "Operations"])
    with tab1:
        st.json(result["output"])
    with tab2:
        for item in result.get("evidence", []):
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
                r = api_post(f"/api/v1/runs/{result['run_id']}/approval", {"decision": "approved", "reviewer": reviewer, "note": note})
                st.success("Approval recorded in the audit trail.") if r.ok else st.error(r.text)
            if b.button("Reject", use_container_width=True):
                r = api_post(f"/api/v1/runs/{result['run_id']}/approval", {"decision": "rejected", "reviewer": reviewer, "note": note})
                st.warning("Rejection recorded in the audit trail.") if r.ok else st.error(r.text)

st.divider()
st.subheader("Recent executions")
try:
    runs = response_json(api_get("/api/v1/runs?limit=10"), "Run history")["runs"]
    if not runs:
        st.caption("No executions yet. Scheduled runs will also appear here.")
    for run in runs:
        created = run["created_at"]
        if isinstance(created, str):
            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
        label = f"{run['workflow']} · {run['status']} · {run['duration_ms']} ms · {created.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        with st.expander(label):
            st.write(run["run_id"])
            st.json(run["output"])
except Exception as exc:
    st.caption(f"Run history unavailable: {exc}")

st.divider()
st.caption("Observability: Prometheus → Grafana · traces: OpenTelemetry → Jaeger")
