"""Small fallback bridge for Streamlit sidebar controls.

The primary toggle is Streamlit's native control, styled in ui_theme.py. The
component remains as a fallback for browser versions where the native control
is present in the DOM but not clickable because of a stale layout layer.
"""
from __future__ import annotations

import streamlit.components.v1 as components


def render_sidebar_toggle() -> None:
    """Render a minimal fallback bridge without covering the application UI."""
    components.html(
        """
        <script>
          (() => {
            const doc = window.parent.document;
            const selectors = [
              '[data-testid="stSidebarCollapsedControl"] button',
              '[data-testid="stSidebarCollapsedControl"]',
              '[data-testid="stSidebarCollapseButton"] button',
              '[data-testid="stSidebarCollapseButton"]',
              '[data-testid="collapsedControl"] button',
              '[data-testid="collapsedControl"]'
            ];
            const repair = () => {
              for (const selector of selectors) {
                const target = doc.querySelector(selector);
                if (target) {
                  target.style.pointerEvents = 'auto';
                  target.style.zIndex = '100001';
                }
              }
            };
            repair();
            window.setInterval(repair, 1200);
          })();
        </script>
        """,
        height=1,
        width=1,
    )
