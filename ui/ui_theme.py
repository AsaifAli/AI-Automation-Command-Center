"""Shared premium Streamlit visual layer for FlowPilot.

Presentation-only: does not change application state, API calls, or business logic.
"""
from __future__ import annotations

import streamlit as st


def apply_theme() -> None:
    """Apply the polished FlowPilot visual system."""
    try:
        mode = st.context.theme.type or "light"
    except Exception:
        mode = "light"
    dark = mode.lower() == "dark"

    if dark:
        tokens = dict(
            page="#070b13", surface="#0d1422", surface2="#111a2a", border="rgba(148,163,184,.18)",
            border_strong="rgba(148,163,184,.28)", text="#f3f7fb", text2="#d6e0ec", muted="#91a0b2",
            faint="#718096", field="#0d1422", shadow="0 18px 48px rgba(0,0,0,.24)", tint="rgba(37,99,235,.10)",
            sidebar="#0a101b",
        )
    else:
        tokens = dict(
            page="#f5f7fb", surface="#ffffff", surface2="#f3f5fa", border="rgba(100,116,139,.20)",
            border_strong="rgba(100,116,139,.30)", text="#172033", text2="#334155", muted="#64748b",
            faint="#7b8797", field="#ffffff", shadow="0 18px 48px rgba(15,23,42,.08)", tint="rgba(37,99,235,.07)",
            sidebar="#f7f8fc",
        )

    css = f"""
    <style>
    :root {{
      --ui-page:{tokens['page']}; --ui-surface:{tokens['surface']}; --ui-surface-2:{tokens['surface2']};
      --ui-border:{tokens['border']}; --ui-border-strong:{tokens['border_strong']};
      --ui-text:{tokens['text']}; --ui-text-2:{tokens['text2']}; --ui-muted:{tokens['muted']};
      --ui-faint:{tokens['faint']}; --ui-field:{tokens['field']}; --ui-shadow:{tokens['shadow']};
      --ui-accent:#2563eb; --ui-accent-2:#7c3aed; --ui-tint:{tokens['tint']}; --ui-sidebar:{tokens['sidebar']};
    }}
    html, body, [data-testid="stAppViewContainer"], [data-testid="stMainBlockContainer"] {{
      color:var(--ui-text); background:var(--ui-page);
      font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    }}
    body {{ overflow-x:hidden; }}
    [data-testid="stAppViewContainer"] > .main {{
      background:
        radial-gradient(900px 520px at 3% -8%, color-mix(in srgb,var(--ui-accent) 8%,transparent),transparent 62%),
        radial-gradient(760px 460px at 97% 0%, color-mix(in srgb,var(--ui-accent-2) 7%,transparent),transparent 60%),
        var(--ui-page);
    }}
    [data-testid="stHeader"] {{ background:transparent; }}

    /* Never hide Streamlit's native sidebar controls. This covers current and legacy selectors. */
    [data-testid="stSidebarCollapseButton"], [data-testid="stSidebarCollapseButton"] button,
    [data-testid="collapsedControl"], [data-testid="collapsedControl"] button {{
      display:flex !important; visibility:visible !important; opacity:1 !important;
      pointer-events:auto !important; z-index:100000 !important;
    }}
    [data-testid="stToolbar"] {{ visibility:hidden; height:0; }}
    #MainMenu, footer {{ visibility:hidden; }}
    .block-container {{ max-width:1460px; padding-top:1.25rem; padding-bottom:5.5rem;
      padding-left:clamp(1rem,3vw,3rem); padding-right:clamp(1rem,3vw,3rem); }}

    /* Typography */
    [data-testid="stCaptionContainer"], .premium-muted {{ color:var(--ui-muted) !important; }}
    [data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li, label {{ color:var(--ui-text); }}
    h1,h2,h3,h4 {{ color:var(--ui-text)!important; letter-spacing:-.025em; }}
    h1 {{ font-weight:850; }} h2,h3 {{ font-weight:780; }}
    [data-testid="stMetricValue"] {{ font-weight:850; color:var(--ui-text)!important; }}

    /* Sidebar */
    section[data-testid="stSidebar"] {{
      background:linear-gradient(180deg,color-mix(in srgb,var(--ui-accent) 4%,var(--ui-sidebar)),var(--ui-sidebar))!important;
      border-right:1px solid var(--ui-border)!important;
      transition:margin-left .25s ease,width .25s ease;
    }}
    section[data-testid="stSidebar"] > div {{ background:transparent!important; }}
    section[data-testid="stSidebar"] .block-container {{ padding-top:1.15rem; }}
    section[data-testid="stSidebar"] [data-testid="stRadio"] label {{
      border:1px solid transparent; border-radius:12px; padding:.45rem .55rem;
      transition:background .16s ease,border-color .16s ease,transform .16s ease;
    }}
    section[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {{
      background:var(--ui-tint); border-color:var(--ui-border); transform:translateX(2px);
    }}
    section[data-testid="stSidebar"] hr {{ border-color:var(--ui-border); }}

    /* Buttons */
    .stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] button {{
      min-height:40px; border-radius:12px!important; border:1px solid var(--ui-border)!important;
      background:var(--ui-surface)!important; color:var(--ui-text)!important;
      box-shadow:0 4px 14px color-mix(in srgb,#0f172a 5%,transparent);
      font-weight:680; transition:transform .16s ease,box-shadow .16s ease,border-color .16s ease;
    }}
    .stButton > button:hover, .stDownloadButton > button:hover, [data-testid="stFormSubmitButton"] button:hover {{
      transform:translateY(-1px); border-color:color-mix(in srgb,var(--ui-accent) 45%,var(--ui-border))!important;
      box-shadow:0 10px 26px color-mix(in srgb,var(--ui-accent) 10%,transparent);
    }}
    .stButton > button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"] {{
      background:linear-gradient(135deg,var(--ui-accent),var(--ui-accent-2))!important; color:#fff!important;
      border-color:transparent!important; box-shadow:0 12px 30px color-mix(in srgb,var(--ui-accent) 18%,transparent);
    }}

    /* Inputs / selects */
    div[data-baseweb="input"] > div, div[data-baseweb="textarea"] > div, div[data-baseweb="select"] > div,
    div[data-testid="stTextInput"] input, div[data-testid="stTextArea"] textarea {{
      background:var(--ui-field)!important; color:var(--ui-text)!important;
      border-color:var(--ui-border)!important; border-radius:12px!important;
    }}
    div[data-baseweb="input"] > div:focus-within, div[data-baseweb="textarea"] > div:focus-within,
    div[data-baseweb="select"] > div:focus-within {{
      border-color:var(--ui-accent)!important; box-shadow:0 0 0 3px var(--ui-tint)!important;
    }}
    [data-baseweb="menu"] {{ background:var(--ui-surface)!important; border:1px solid var(--ui-border)!important; }}
    [data-baseweb="menu"] li {{ color:var(--ui-text)!important; }}

    /* Metrics / tabs / expanders / alerts */
    [data-testid="stMetric"] {{
      background:linear-gradient(180deg,var(--ui-surface),var(--ui-surface-2));
      border:1px solid var(--ui-border); border-radius:16px; padding:.85rem 1rem;
      box-shadow:var(--ui-shadow); transition:transform .18s ease,box-shadow .18s ease; animation:uiFadeUp .42s ease both;
    }}
    [data-testid="stMetric"]:hover {{ transform:translateY(-2px); }}
    [data-testid="stMetricLabel"] {{ color:var(--ui-muted)!important; font-weight:650; }}
    [data-testid="stExpander"] {{ border:1px solid var(--ui-border)!important; border-radius:14px!important;
      background:var(--ui-surface)!important; overflow:hidden; }}
    [data-testid="stExpander"] summary:hover {{ background:var(--ui-tint); }}
    [data-testid="stTabs"] [role="tab"] {{ font-weight:700; color:var(--ui-muted); }}
    [data-testid="stTabs"] [aria-selected="true"] {{ color:var(--ui-text); }}
    [data-testid="stAlert"] {{ border-radius:14px!important; border:1px solid var(--ui-border)!important;
      box-shadow:0 6px 20px color-mix(in srgb,#0f172a 5%,transparent); animation:uiFadeUp .35s ease both; }}
    [data-testid="stProgress"] > div > div {{ border-radius:999px; }}

    /* Product-specific surfaces */
    .premium-hero {{
      position:relative; overflow:hidden; margin:.2rem 0 1.2rem; padding:1.35rem 1.45rem;
      border:1px solid var(--ui-border); border-radius:22px;
      background:linear-gradient(135deg,color-mix(in srgb,var(--ui-accent) 7%,var(--ui-surface)),
        color-mix(in srgb,var(--ui-accent-2) 5%,var(--ui-surface)));
      box-shadow:var(--ui-shadow); animation:uiFadeUp .5s ease both;
    }}
    .premium-hero::after {{ content:""; position:absolute; width:260px; height:260px; right:-100px; top:-150px;
      border-radius:50%; background:color-mix(in srgb,var(--ui-accent) 12%,transparent); filter:blur(2px); }}
    .premium-kicker {{ text-transform:uppercase; letter-spacing:.16em; font-size:.68rem; font-weight:850;
      color:var(--ui-accent); margin-bottom:.35rem; }}
    .premium-title {{ font-size:clamp(1.65rem,3vw,2.25rem); line-height:1.08; font-weight:850;
      letter-spacing:-.04em; color:var(--ui-text); margin:0; }}
    .premium-subtitle {{ margin:.55rem 0 0; max-width:880px; color:var(--ui-muted); font-size:.94rem; line-height:1.55; }}
    .premium-strip {{ display:flex; flex-wrap:wrap; gap:.45rem; margin:.8rem 0 0; }}
    .premium-chip {{ display:inline-flex; align-items:center; gap:.42rem; padding:.36rem .65rem; border-radius:999px;
      border:1px solid var(--ui-border); background:var(--ui-tint); color:var(--ui-text-2); font-size:.72rem; font-weight:750; }}
    .premium-dot {{ width:6px; height:6px; border-radius:50%; background:var(--ui-accent); box-shadow:0 0 0 4px color-mix(in srgb,var(--ui-accent) 10%,transparent); }}
    .section-kicker {{ margin:.9rem 0 .35rem; text-transform:uppercase; letter-spacing:.16em; font-size:.67rem;
      font-weight:850; color:var(--ui-accent); }}
    .workflow-intro {{ color:var(--ui-muted); font-size:.82rem; margin-bottom:.65rem; }}
    .status-pill {{ display:inline-flex; align-items:center; gap:.4rem; padding:.28rem .58rem; border-radius:999px;
      border:1px solid var(--ui-border); background:var(--ui-tint); color:var(--ui-text-2); font-size:.7rem; font-weight:750; }}
    .status-dot {{ width:7px; height:7px; border-radius:50%; background:#10b981; box-shadow:0 0 0 4px rgba(16,185,129,.10); }}
    .status-dot.warn {{ background:#f59e0b; box-shadow:0 0 0 4px rgba(245,158,11,.10); }}
    .status-dot.fail {{ background:#ef4444; box-shadow:0 0 0 4px rgba(239,68,68,.10); }}
    .ops-panel {{ padding:.7rem .8rem; border:1px solid var(--ui-border); border-radius:14px; background:var(--ui-surface-2); }}
    .ops-label {{ font-size:.65rem; text-transform:uppercase; letter-spacing:.12em; color:var(--ui-muted); font-weight:800; }}
    .ops-value {{ margin-top:.2rem; color:var(--ui-text); font-weight:750; font-size:.78rem; word-break:break-word; }}
    @keyframes uiFadeUp {{ from {{ opacity:0; transform:translateY(6px); }} to {{ opacity:1; transform:none; }} }}
    @media (prefers-reduced-motion:reduce) {{ *,*::before,*::after {{ animation:none!important; transition:none!important; }} }}
    @media (max-width:900px) {{ .block-container {{ padding-left:1rem; padding-right:1rem; }} .premium-hero {{ padding:1.1rem; }} }}
    </style>
    """
    st.html(css)
