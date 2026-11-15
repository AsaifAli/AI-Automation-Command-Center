"""Client-facing Streamlit renderers for FlowPilot workflow results.

Model/API payloads are presented as cards, summaries and tables. Raw JSON is
intentionally not rendered in the client UI.
"""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import Any
from urllib.parse import urlsplit

import streamlit as st
from output_formatting import humanize_key, parse_model_note

CHANNEL_ICONS = {"linkedin": "in", "x": "𝕏", "telegram": "↗"}


def _items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _safe_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return value.strip()


def _render_note(title: str, value: Any, *, info: bool = False) -> None:
    note = parse_model_note(value)
    if not note:
        return
    st.markdown(f"#### {escape(title)}")
    if isinstance(note, Mapping):
        preferred = ("summary", "executive_summary", "recommendation", "guardrail", "caveat", "confidence_note")
        rendered: set[str] = set()
        for key in preferred:
            if key not in note or not str(note[key]).strip():
                continue
            label = humanize_key(key)
            st.markdown(f"**{escape(label)}**")
            st.markdown(str(note[key]), unsafe_allow_html=False)
            rendered.add(key)
        for key, item in note.items():
            if key in rendered or item in (None, "", [], {}):
                continue
            st.markdown(f"**{escape(humanize_key(key))}**")
            if isinstance(item, list):
                for entry in item:
                    st.markdown(f"- {entry!s}")
            else:
                st.markdown(str(item), unsafe_allow_html=False)
    else:
        if info:
            st.info(str(note))
        else:
            st.markdown(str(note), unsafe_allow_html=False)


def _render_content(data: Mapping[str, Any]) -> None:
    posts = _items(data.get("posts"))
    linkedin = sum(str(post.get("channel", "")).lower() == "linkedin" for post in posts)
    x_posts = sum(str(post.get("channel", "")).lower() == "x" for post in posts)
    telegram = sum(str(post.get("channel", "")).lower() == "telegram" for post in posts)
    metric_cols = st.columns(4)
    metric_cols[0].metric("Drafts", len(posts))
    metric_cols[1].metric("LinkedIn", linkedin)
    metric_cols[2].metric("X", x_posts)
    metric_cols[3].metric("Telegram", telegram)

    if not posts:
        st.warning("No content drafts were returned. Review the execution warnings and try again.")
        return

    st.markdown("### Ready for editorial review")
    st.caption("Each draft is channel-specific. Nothing is published automatically.")
    for index, post in enumerate(posts, 1):
        channel = str(post.get("channel", "channel")).lower()
        channel_label = humanize_key(channel)
        draft = str(post.get("draft", "")).strip()
        topic = str(post.get("topic", "")).strip()
        limit = {"x": 280, "linkedin": 1300, "telegram": 800}.get(channel)
        with st.container(border=True):
            top_left, top_right = st.columns([4, 1])
            with top_left:
                st.markdown(f"#### {CHANNEL_ICONS.get(channel, '✦')}  Draft {index} · {escape(channel_label)}")
                if topic:
                    st.caption(topic)
            with top_right:
                st.markdown("<div style='text-align:right;font-weight:700;color:#0f9d75'>NEEDS REVIEW</div>", unsafe_allow_html=True)
            st.markdown(draft or "No draft text was returned.", unsafe_allow_html=False)
            meta_cols = st.columns([1, 1, 2])
            meta_cols[0].caption(f"{len(draft)} characters" + (f" / {limit}" if limit else ""))
            meta_cols[1].caption("Human approval required")
            meta_cols[2].caption("Publishing is disabled until an authorized reviewer approves.")
            with st.expander("Copy-ready draft", expanded=False):
                st.code(draft, language=None)
    _render_note("Editorial direction", data.get("strategy_note"), info=True)


def _render_competitor(data: Mapping[str, Any]) -> None:
    competitors = _items(data.get("competitors"))
    cols = st.columns(3)
    cols[0].metric("Competitors", len(competitors))
    cols[1].metric("Feeds configured", int(data.get("source_count", 0) or 0))
    cols[2].metric("Items observed", int(data.get("observed_item_count", 0) or 0))
    st.markdown("### Intelligence findings")
    if not competitors:
        st.info("No competitor records were returned for this run.")
    for item in competitors:
        entity = str(item.get("entity", "Competitor"))
        priority = str(item.get("priority", "unverified")).lower()
        with st.container(border=True):
            name_col, priority_col = st.columns([3, 1])
            name_col.markdown(f"#### {escape(entity)}")
            priority_col.markdown(f"**{escape(humanize_key(priority))}**")
            implication = str(item.get("implication", "")).strip()
            if implication:
                st.markdown(implication, unsafe_allow_html=False)
            signals = item.get("signals") or []
            if signals:
                st.markdown("**Signals and checks**")
                for signal in signals:
                    st.markdown(f"- {signal!s}")
            observed = _items(item.get("observed_items"))
            st.caption(f"{len(observed)} matching source item(s)")
            for source in observed:
                with st.expander(str(source.get("title", "Observed source"))[:160], expanded=False):
                    st.caption(f"Source: {source.get('source', 'Feed')} · Published: {source.get('published') or 'Date not supplied'}")
                    url = _safe_url(source.get("url"))
                    if url:
                        st.link_button("Open original source", url)
    _render_note("Analyst assessment", data.get("analyst_note"), info=True)
    actions = data.get("recommended_actions") or []
    if actions:
        st.markdown("### Recommended next steps")
        for action in actions:
            st.markdown(f"- {action!s}")
    st.caption("Evidence limitation: feed matching is directional. Validate important findings against primary sources.")


def _render_outreach(data: Mapping[str, Any]) -> None:
    candidates = _items(data.get("candidates"))
    cols = st.columns(3)
    cols[0].metric("Opportunities", len(candidates))
    cols[1].metric("Approval gated", sum(item.get("approval_required") is True for item in candidates))
    cols[2].metric("External sends", 0)
    st.markdown("### Outreach drafts")
    if not candidates:
        st.info("No outreach candidates were returned for review.")
    for item in candidates:
        name = str(item.get("name", "Prospect"))
        with st.container(border=True):
            st.markdown(f"#### {escape(name)}")
            meta_cols = st.columns(3)
            meta_cols[0].caption(f"Channel · {humanize_key(item.get('channel', 'not specified'))}")
            meta_cols[1].caption(f"Qualification · {humanize_key(item.get('qualification', 'review'))}")
            meta_cols[2].caption(f"Follow-up · {item.get('follow_up_days', 5)} days")
            reason = str(item.get("reason", "")).strip()
            if reason:
                st.markdown(reason, unsafe_allow_html=False)
            st.markdown("**Suggested message**")
            st.markdown(str(item.get("message", "Draft not available.")), unsafe_allow_html=False)
            with st.expander("Copy message", expanded=False):
                st.code(str(item.get("message", "")), language=None)
            if item.get("approval_required") is True:
                st.warning("Human approval required before sending. No external action has been taken.")
    _render_note("Qualification notes", data.get("qualification_note"), info=True)
    policy = str(data.get("policy", "No external message is sent automatically."))
    st.info(policy)


def _render_kpi(data: Mapping[str, Any]) -> None:
    updates = _items(data.get("normalized_updates"))
    risks = _items(data.get("risks"))
    on_track = sum(str(item.get("status", "")).lower() == "on_track" for item in updates)
    at_risk = len(risks)
    cols = st.columns(3)
    cols[0].metric("Updates reviewed", len(updates))
    cols[1].metric("On track", on_track)
    cols[2].metric("At risk", at_risk, delta=f"{at_risk} blocker(s)" if at_risk else "No blockers reported", delta_color="inverse")

    brief = str(data.get("leadership_brief", "")).strip()
    if brief:
        st.markdown("### Leadership snapshot")
        st.info(brief)

    st.markdown("### Performance updates")
    if updates:
        table = [
            {
                "Entity": item.get("entity", "Unknown"),
                "Metric": item.get("metric", "Unspecified"),
                "Latest value": item.get("value", "Not reported"),
                "Status": humanize_key(item.get("status", "unknown")),
                "Blocker": item.get("blocker", "None reported"),
            }
            for item in updates
        ]
        st.dataframe(table, hide_index=True, use_container_width=True)
    else:
        st.caption("No KPI updates were supplied.")

    st.markdown("### Risks and blockers")
    if risks:
        for item in risks:
            with st.container(border=True):
                st.error(f"{item.get('entity', 'Entity')} · {item.get('metric', 'Metric')}")
                st.markdown(f"**Current value:** {item.get('value', 'Not reported')}")
                st.markdown(f"**Blocker:** {item.get('blocker', 'Risk reported')}")
    else:
        st.success("No blockers were reported in the submitted updates.")
    _render_note("Executive commentary", data.get("executive_note"), info=True)


def render_workflow_output(workflow: str, output: Any) -> None:
    """Render workflow output as a polished client-facing view, never raw JSON."""
    data = dict(output) if isinstance(output, Mapping) else {"result": output}
    name = str(workflow or "").lower()
    if data.get("error"):
        st.error("This workflow could not be completed.")
        st.caption(
            "Retry the workflow. If the issue persists, share its run reference with an administrator."
        )
        return
    if name == "content":
        _render_content(data)
    elif name == "competitor":
        _render_competitor(data)
    elif name == "outreach":
        _render_outreach(data)
    elif name == "kpi":
        _render_kpi(data)
    else:
        st.markdown("### Execution summary")
        for key, value in data.items():
            if value in (None, "", [], {}):
                continue
            st.markdown(f"**{escape(humanize_key(key))}**")
            if isinstance(value, list):
                for item in value:
                    st.markdown(f"- {item!s}")
            elif isinstance(value, Mapping):
                for nested_key, nested_value in value.items():
                    st.markdown(f"- **{escape(humanize_key(nested_key))}:** {nested_value!s}")
            else:
                st.markdown(str(value), unsafe_allow_html=False)
