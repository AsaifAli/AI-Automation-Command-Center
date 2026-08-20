"""Shared premium Streamlit visual layer for FlowPilot."""
from __future__ import annotations

import streamlit as st


def apply_theme() -> None:
    """Apply the polished FlowPilot visual system without changing app logic."""
    try:
        mode = st.context.theme.type or "light"
    except Exception:
        mode = "light"
    dark = mode.lower() == "dark"

    if dark:
        tokens = dict(
            page="#070b13",
            surface="#0d1422",
            surface2="#111a2a",
            border="rgba(148,163,184,.18)",
            border_strong="rgba(148,163,184,.28)",
            text="#f3f7fb",
            text2="#d6e0ec",
            muted="#91a0b2",
            faint="#718096",
            field="#0d1422",
            shadow="0 18px 48px rgba(0,0,0,.24)",
            tint="rgba(37,99,235,.10)",
            sidebar="#0a101b",
        )
    else:
        tokens = dict(
            page="#f5f7fb",
            surface="#ffffff",
            surface2="#f3f5fa",
            border="rgba(100,116,139,.20)",
            border_strong="rgba(100,116,139,.30)",
            text="#172033",
            text2="#334155",
            muted="#64748b",
            faint="#7b8797",
            field="#ffffff",
            shadow="0 18px 48px rgba(15,23,42,.08)",
            tint="rgba(37,99,235,.07)",
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
    [data-testid="stToolbar"] {{ background:transparent; }}
    #MainMenu, footer {{ visibility:hidden; }}
    .block-container {{ max-width:1480px; padding-top:1rem; padding-bottom:4rem;
      padding-left:clamp(1rem,2.6vw,2.8rem); padding-right:clamp(1rem,2.6vw,2.8rem); }}

    /* Sidebar controls: keep both current and legacy Streamlit selectors usable. */
    [data-testid="stSidebarCollapseButton"], [data-testid="stSidebarCollapseButton"] button,
    [data-testid="stSidebarCollapsedControl"], [data-testid="stSidebarCollapsedControl"] button,
    [data-testid="collapsedControl"], [data-testid="collapsedControl"] button {{
      z-index:100001 !important; pointer-events:auto !important;
    }}
    [data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"] {{
      position:fixed !important; top:.62rem !important; left:.62rem !important;
      display:flex !important; visibility:visible !important; opacity:1 !important;
      width:42px !important; height:42px !important; align-items:center; justify-content:center;
    }}
    [data-testid="stSidebarCollapsedControl"] button, [data-testid="collapsedControl"] button {{
      width:40px !important; height:40px !important; border-radius:12px !important;
      background:color-mix(in srgb,var(--ui-surface) 92%,transparent) !important;
      border:1px solid var(--ui-border) !important; box-shadow:0 8px 24px rgba(0,0,0,.16) !important;
    }}
    [data-testid="stSidebarCollapsedControl"] svg, [data-testid="collapsedControl"] svg {{
      width:20px !important; height:20px !important;
    }}
    [data-testid="stSidebarCollapseButton"] {{ margin-left:.2rem !important; }}
    [data-testid="stIFrame"] {{ position:fixed !important; width:1px !important; height:1px !important;
      opacity:0 !important; pointer-events:none !important; }}

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
    }}
    section[data-testid="stSidebar"] > div {{ background:transparent!important; }}
    section[data-testid="stSidebar"] .block-container {{ padding-top:1rem; }}
    section[data-testid="stSidebar"] [data-testid="stRadio"] label {{
      border:1px solid transparent; border-radius:12px; padding:.45rem .55rem;
      transition:background .16s ease,border-color .16s ease,transform .16s ease;
    }}
    section[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {{
      background:var(--ui-tint); border-color:var(--ui-border); transform:translateX(2px);
    }}
    section[data-testid="stSidebar"] hr {{ border-color:var(--ui-border); }}
    .sidebar-brand {{ display:flex; align-items:center; gap:.65rem; padding:.2rem 0 1.1rem; }}
    .sidebar-brand-mark {{ display:grid; place-items:center; width:34px; height:34px; border-radius:11px;
      background:linear-gradient(135deg,var(--ui-accent),var(--ui-accent-2)); color:#fff; box-shadow:0 8px 22px color-mix(in srgb,var(--ui-accent) 20%,transparent); }}
    .sidebar-brand strong {{ display:block; color:var(--ui-text); font-size:.78rem; letter-spacing:.08em; }}
    .sidebar-brand small {{ display:block; color:var(--ui-muted); font-size:.6rem; letter-spacing:.12em; margin-top:.1rem; }}
    .sidebar-health {{ display:grid; gap:.3rem; padding:.7rem .75rem; border:1px solid var(--ui-border); border-radius:14px; background:var(--ui-surface); }}
    .sidebar-health > div {{ display:flex; align-items:center; gap:.45rem; color:var(--ui-text-2); font-size:.76rem; font-weight:750; }}
    .sidebar-health small {{ color:var(--ui-muted); font-size:.65rem; }}
    .sidebar-footer-note {{ color:var(--ui-faint); font-size:.64rem; line-height:1.5; padding:.15rem .1rem; }}

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

    /* Core surfaces */
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

    /* Hero */
    .premium-hero {{
      position:relative; overflow:hidden; margin:.1rem 0 1.15rem; padding:1.3rem 1.45rem;
      border:1px solid var(--ui-border); border-radius:22px;
      background:linear-gradient(135deg,color-mix(in srgb,var(--ui-accent) 7%,var(--ui-surface)),color-mix(in srgb,var(--ui-accent-2) 5%,var(--ui-surface)));
      box-shadow:var(--ui-shadow); animation:uiFadeUp .5s ease both;
    }}
    .premium-hero::after {{ content:""; position:absolute; width:280px; height:280px; right:-110px; top:-155px;
      border-radius:50%; background:color-mix(in srgb,var(--ui-accent) 12%,transparent); filter:blur(2px); }}
    .hero-topline {{ display:flex; align-items:center; justify-content:space-between; gap:1rem; position:relative; z-index:1; }}
    .environment-badge {{ display:inline-flex; align-items:center; gap:.38rem; padding:.3rem .55rem; border:1px solid var(--ui-border);
      border-radius:999px; background:var(--ui-tint); color:var(--ui-text-2); font-size:.62rem; font-weight:800; letter-spacing:.06em; }}
    .premium-kicker {{ text-transform:uppercase; letter-spacing:.16em; font-size:.66rem; font-weight:850; color:var(--ui-accent); margin-bottom:.35rem; }}
    .premium-title {{ font-size:clamp(1.65rem,3vw,2.3rem); line-height:1.08; font-weight:850; letter-spacing:-.04em; color:var(--ui-text); margin:0; position:relative; z-index:1; }}
    .premium-subtitle {{ margin:.55rem 0 0; max-width:900px; color:var(--ui-muted); font-size:.91rem; line-height:1.55; position:relative; z-index:1; }}
    .premium-strip {{ display:flex; flex-wrap:wrap; gap:.45rem; margin:.8rem 0 0; position:relative; z-index:1; }}
    .premium-chip {{ display:inline-flex; align-items:center; gap:.42rem; padding:.36rem .65rem; border-radius:999px;
      border:1px solid var(--ui-border); background:var(--ui-tint); color:var(--ui-text-2); font-size:.7rem; font-weight:750; }}
    .premium-dot {{ width:6px; height:6px; border-radius:50%; background:var(--ui-accent); box-shadow:0 0 0 4px color-mix(in srgb,var(--ui-accent) 10%,transparent); }}

    /* Dashboard cards */
    .section-kicker {{ margin:.75rem 0 .38rem; text-transform:uppercase; letter-spacing:.16em; font-size:.65rem; font-weight:850; color:var(--ui-accent); }}
    .subsection-title {{ color:var(--ui-text); font-size:1.15rem; font-weight:800; letter-spacing:-.025em; margin:.05rem 0 .6rem; }}
    .pulse-card {{ min-height:94px; padding:.78rem .9rem; border:1px solid var(--ui-border); border-radius:16px;
      background:linear-gradient(180deg,var(--ui-surface),var(--ui-surface-2)); box-shadow:var(--ui-shadow); animation:uiFadeUp .42s ease both; }}
    .pulse-label {{ color:var(--ui-muted); font-size:.63rem; font-weight:800; letter-spacing:.12em; }}
    .pulse-value {{ margin-top:.2rem; color:var(--ui-text); font-size:1.45rem; line-height:1.1; font-weight:850; letter-spacing:-.03em; }}
    .pulse-value.online {{ color:#10b981; }}
    .pulse-detail {{ margin-top:.24rem; color:var(--ui-faint); font-size:.63rem; }}
    .workflow-heading {{ display:flex; gap:.75rem; align-items:flex-start; margin:.1rem 0 .9rem; }}
    .workflow-icon {{ display:grid; place-items:center; width:42px; height:42px; flex:0 0 42px; border:1px solid var(--ui-border);
      border-radius:13px; background:var(--ui-tint); font-size:1.15rem; }}
    .workflow-heading h2 {{ margin:0!important; font-size:1.45rem; }}
    .workflow-heading p {{ margin:.22rem 0 0; color:var(--ui-muted); font-size:.78rem; line-height:1.45; }}
    .config-note {{ color:var(--ui-faint); font-size:.66rem; margin:.65rem 0 .5rem; }}

    /* Workflow preview */
    .workflow-preview {{ padding:1rem; border:1px solid var(--ui-border); border-radius:18px; background:linear-gradient(180deg,var(--ui-surface),var(--ui-surface-2)); box-shadow:var(--ui-shadow); }}
    .preview-head {{ display:flex; justify-content:space-between; align-items:flex-start; gap:.75rem; }}
    .preview-title {{ color:var(--ui-text); font-size:1rem; font-weight:800; margin-top:.12rem; }}
    .ready-badge {{ display:inline-flex; align-items:center; gap:.35rem; padding:.26rem .48rem; border:1px solid rgba(16,185,129,.2); border-radius:999px; color:#10b981; background:rgba(16,185,129,.07); font-size:.6rem; font-weight:850; }}
    .preview-output, .preview-footer {{ display:flex; justify-content:space-between; gap:1rem; padding:.65rem 0; border-bottom:1px solid var(--ui-border); font-size:.68rem; }}
    .preview-output span, .preview-footer span {{ color:var(--ui-muted); }}
    .preview-output strong, .preview-footer strong {{ color:var(--ui-text-2); }}
    .workflow-steps {{ display:grid; gap:.38rem; padding:.75rem 0; }}
    .workflow-step {{ display:flex; align-items:center; gap:.55rem; color:var(--ui-text-2); font-size:.71rem; }}
    .workflow-step-index {{ display:grid; place-items:center; width:23px; height:23px; border-radius:8px; background:var(--ui-tint); color:var(--ui-accent); font-size:.57rem; font-weight:850; }}

    /* Status / operations */
    .status-pill {{ display:inline-flex; align-items:center; gap:.4rem; padding:.28rem .58rem; border-radius:999px; border:1px solid var(--ui-border); background:var(--ui-tint); color:var(--ui-text-2); font-size:.68rem; font-weight:800; }}
    .status-dot {{ display:inline-block; width:7px; height:7px; flex:0 0 7px; border-radius:50%; background:#10b981; box-shadow:0 0 0 4px rgba(16,185,129,.10); }}
    .status-dot.warn {{ background:#f59e0b; box-shadow:0 0 0 4px rgba(245,158,11,.10); }}
    .status-dot.fail {{ background:#ef4444; box-shadow:0 0 0 4px rgba(239,68,68,.10); }}
    .live-card {{ padding:.95rem 1rem; border:1px solid var(--ui-border); border-radius:16px; background:var(--ui-surface); }}
    .live-card-top {{ display:flex; justify-content:space-between; gap:1rem; align-items:flex-start; }}
    .live-title {{ margin-top:.18rem; color:var(--ui-text); font-weight:800; }}
    .run-id {{ color:var(--ui-faint); font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.62rem; }}
    .live-status {{ margin-top:.8rem; }}
    .ops-label {{ font-size:.62rem; text-transform:uppercase; letter-spacing:.12em; color:var(--ui-muted); font-weight:800; }}
    .ops-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:.55rem; }}
    .ops-panel {{ padding:.7rem .8rem; border:1px solid var(--ui-border); border-radius:14px; background:var(--ui-surface-2); }}
    .ops-value {{ margin-top:.2rem; color:var(--ui-text); font-weight:750; font-size:.78rem; word-break:break-word; }}
    .health-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:.55rem; }}
    .health-item {{ display:flex; flex-direction:column; gap:.38rem; padding:.72rem .78rem; border:1px solid var(--ui-border); border-radius:14px; background:var(--ui-surface); }}
    .health-item span {{ display:flex; align-items:center; gap:.4rem; color:var(--ui-muted); font-size:.66rem; }}
    .health-item strong {{ color:var(--ui-text-2); font-size:.74rem; }}
    .approval-card {{ display:flex; align-items:center; gap:.75rem; min-height:74px; padding:.75rem .85rem; border:1px solid var(--ui-border); border-radius:14px; background:var(--ui-surface); }}
    .approval-card.muted {{ background:var(--ui-surface-2); }}
    .approval-number {{ color:var(--ui-text); font-size:1.55rem; line-height:1; font-weight:850; }}
    .approval-card strong {{ display:block; color:var(--ui-text-2); font-size:.72rem; }}
    .approval-card small {{ display:block; margin-top:.15rem; color:var(--ui-muted); font-size:.62rem; }}

    /* History */
    .execution-row {{ display:grid; grid-template-columns:42px 1fr auto; gap:.75rem; align-items:center; padding:.8rem .9rem; margin:.42rem 0; border:1px solid var(--ui-border); border-radius:15px; background:var(--ui-surface); transition:transform .16s ease,border-color .16s ease,box-shadow .16s ease; }}
    .execution-row:hover {{ transform:translateY(-1px); border-color:color-mix(in srgb,var(--ui-accent) 35%,var(--ui-border)); box-shadow:0 8px 22px color-mix(in srgb,#0f172a 6%,transparent); }}
    .execution-icon {{ display:grid; place-items:center; width:36px; height:36px; border-radius:11px; background:var(--ui-tint); border:1px solid var(--ui-border); }}
    .execution-main strong {{ display:block; color:var(--ui-text-2); font-size:.75rem; }}
    .execution-main span {{ display:block; margin-top:.16rem; color:var(--ui-muted); font-size:.62rem; }}
    .execution-status {{ display:flex; flex-direction:column; align-items:flex-end; gap:.22rem; }}
    .execution-status small {{ color:var(--ui-faint); font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.58rem; }}
    .empty-state {{ display:flex; flex-direction:column; gap:.2rem; padding:1.2rem; border:1px dashed var(--ui-border-strong); border-radius:15px; background:var(--ui-surface-2); text-align:center; }}
    .empty-state strong {{ color:var(--ui-text-2); font-size:.78rem; }}
    .empty-state span {{ color:var(--ui-muted); font-size:.65rem; }}
    .footer-note {{ text-align:center; font-size:.66rem; padding:.8rem 0 0; }}

    @keyframes uiFadeUp {{ from {{ opacity:0; transform:translateY(6px); }} to {{ opacity:1; transform:none; }} }}
    @media (prefers-reduced-motion:reduce) {{ *,*::before,*::after {{ animation:none!important; transition:none!important; }} }}
    @media (max-width:900px) {{
      .block-container {{ padding-left:1rem; padding-right:1rem; }}
      .premium-hero {{ padding:1.1rem; }}
      .health-grid {{ grid-template-columns:1fr 1fr; }}
    }}
    @media (max-width:640px) {{
      .hero-topline {{ align-items:flex-start; flex-direction:column; }}
      .health-grid {{ grid-template-columns:1fr; }}
      .execution-row {{ grid-template-columns:36px 1fr; }}
      .execution-status {{ grid-column:2; align-items:flex-start; }}
    }}
    </style>
    """
    st.html(css)
