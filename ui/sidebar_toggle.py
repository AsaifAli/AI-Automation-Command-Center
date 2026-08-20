"""Reliable always-visible Streamlit sidebar toggle."""
from __future__ import annotations

import streamlit.components.v1 as components


def render_sidebar_toggle() -> None:
    """Render a visible button that forwards clicks to Streamlit's native toggle."""
    components.html(
        """
        <style>
          html, body { margin: 0; padding: 0; background: transparent; overflow: hidden; }
          .toggle {
            width: 40px; height: 40px; border-radius: 12px;
            border: 1px solid rgba(148,163,184,.24);
            background: rgba(13,20,34,.94); color: #dbe7f5;
            display: grid; place-items: center; cursor: pointer;
            box-shadow: 0 8px 24px rgba(0,0,0,.18);
            backdrop-filter: blur(10px);
            transition: transform .16s ease, border-color .16s ease, background .16s ease;
          }
          .toggle:hover { transform: translateY(-1px); border-color: rgba(37,99,235,.65); background: rgba(20,30,48,.98); }
          .toggle:active { transform: translateY(0) scale(.97); }
          .icon { width: 18px; height: 18px; position: relative; }
          .icon::before, .icon::after, .icon span {
            content: ""; position: absolute; left: 2px; width: 14px; height: 2px;
            border-radius: 2px; background: currentColor;
          }
          .icon::before { top: 3px; } .icon span { top: 8px; } .icon::after { top: 13px; }
        </style>
        <button class="toggle" type="button" aria-label="Toggle sidebar" title="Toggle sidebar">
          <span class="icon"><span></span></span>
        </button>
        <script>
          const button = document.querySelector('.toggle');
          button.addEventListener('click', () => {
            const doc = window.parent.document;
            const selectors = [
              '[data-testid="stSidebarCollapseButton"] button',
              '[data-testid="stSidebarCollapseButton"]',
              '[data-testid="collapsedControl"] button',
              '[data-testid="collapsedControl"]',
              '[data-testid="stSidebarCollapsedControl"] button',
              '[data-testid="stSidebarCollapsedControl"]'
            ];
            for (const selector of selectors) {
              const target = doc.querySelector(selector);
              if (target) { target.click(); return; }
            }
          });
        </script>
        """,
        height=44,
        width=44,
    )
