"""Retired standalone FlowPilot UI.

The product is now available only from the authenticated Studio workspace.
This minimal landing page intentionally makes no product API calls and
cannot launch workflows outside the workspace's BYOK and session boundary.
"""

import streamlit as st
from legacy_notice import legacy_workspace_url

st.set_page_config(
    page_title="FlowPilot · Studio Workspace",
    page_icon="↗",
    layout="centered",
)

st.title("FlowPilot has moved")
st.write(
    "The standalone FlowPilot interface has been retired. "
    "Open FlowPilot from the authenticated AI Engineering Studio workspace "
    "to use the shared frontend, central BYOK connection and secure app session."
)
st.link_button(
    "Open the Studio workspace",
    legacy_workspace_url(),
    type="primary",
)
st.caption(
    "Sign in to your client workspace, connect your own provider key once, "
    "then launch FlowPilot from Applications."
)
