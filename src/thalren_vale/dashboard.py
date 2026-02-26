"""
dashboard.py — Streamlit live dashboard for the AI Sandbox civilization simulation.

Launch:
    streamlit run dashboard.py

Reads only dashboard_data.json — no sim modules imported.
Auto-refreshes at 2 FPS via streamlit-autorefresh (falls back to a
manual Refresh button when the package is not installed).
"""

import json
import pathlib
import numpy as np
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

# ── Optional: streamlit-autorefresh for 2-FPS polling ────────────────────
try:
    from streamlit_autorefresh import st_autorefresh as _st_autorefresh
    _HAS_AUTOREFRESH = True
except ImportError:
    _HAS_AUTOREFRESH = False

DATA_PATH = pathlib.Path("dashboard_data.json")

# ── Biome palette (indexed by biome_id) ───────────────────────────────────
# Order matches world.py BIOMES: forest=0 plains=1 mountains=2 desert=3 coast=4 sea=5
_BIOME_RGB: list[tuple[int, int, int]] = [
    ( 34, 139,  34),   # 0  forest     — dark green
    (154, 205,  50),   # 1  plains     — yellow-green
    (105, 105, 105),   # 2  mountains  — dim gray
    (210, 180, 140),   # 3  desert     — tan / sandstone
    ( 95, 158, 160),   # 4  coast      — cadet blue
    ( 25,  95, 180),   # 5  sea        — medium blue
]
_BIOME_NAMES = ['Forest', 'Plains', 'Mountains', 'Desert', 'Coast', 'Sea']

def _rgb_hex(rgb: tuple[int, int, int]) -> str:
    return '#{:02x}{:02x}{:02x}'.format(*rgb)

_BIOME_HEX = [_rgb_hex(c) for c in _BIOME_RGB]

# Up to 10 distinct faction overlay colours
_FACTION_COLORS = [
    '#FF4B4B', '#FFB347', '#FAFF66', '#66FF99',
    '#66ECFF', '#6699FF', '#CC66FF', '#FF66C0',
    '#FFFFFF', '#AAAAAA',
]


# ══════════════════════════════════════════════════════════════════════════
# Data loading — TTL-cached so we don't hammer disk on every Streamlit run
# ══════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=2)
def _read_json(mtime: float) -> dict | None:          # mtime is the cache-bust key
    try:
        return json.loads(DATA_PATH.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_data() -> dict | None:
    try:
        mtime = DATA_PATH.stat().st_mtime
    except FileNotFoundError:
        return None
    return _read_json(mtime)


# ══════════════════════════════════════════════════════════════════════════
# World map figure
# ══════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=10)
def _biome_image(grid_json: str) -> np.ndarray:
    """Convert serialised biome_grid → H×W×3 uint8 RGB array (cached 10 s)."""
    grid = json.loads(grid_json)
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    img  = np.zeros((rows, cols, 3), dtype=np.uint8)
    for r in range(rows):
        for c in range(cols):
            bid      = int(grid[r][c])
            img[r, c] = _BIOME_RGB[bid] if bid < len(_BIOME_RGB) else _BIOME_RGB[1]
    return img


def build_world_map(data: dict) -> go.Figure:
    """Plotly figure: biome RGB image + per-faction member scatter overlays."""
    grid_json = json.dumps(data['biome_grid'])
    img       = _biome_image(grid_json)

    fig = px.imshow(img, origin='upper', aspect='equal')
    fig.update_layout(
        coloraxis_showscale=False,
        paper_bgcolor='#0e1117',
        plot_bgcolor='#0e1117',
        margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(showticklabels=False, showgrid=False, zeroline=False,
                   range=[-0.5, img.shape[1] - 0.5]),
        yaxis=dict(showticklabels=False, showgrid=False, zeroline=False,
                   range=[img.shape[0] - 0.5, -0.5]),
        height=420,
        legend=dict(
            bgcolor='rgba(14,17,23,0.75)', font=dict(color='white', size=11),
            x=1.01, y=1, xanchor='left',
        ),
    )

    for idx, f in enumerate(data.get('factions', [])[:10]):
        if not f['members']:
            continue
        color = _FACTION_COLORS[idx % len(_FACTION_COLORS)]
        xs    = [pos[1] + 0.5 for pos in f['members']]   # column → x
        ys    = [pos[0] + 0.5 for pos in f['members']]   # row    → y
        fig.add_trace(go.Scatter(
            x=xs, y=ys,
            mode='markers',
            marker=dict(size=7, color=color, opacity=0.85,
                        line=dict(width=0.8, color='white')),
            name=f['name'],
            hovertemplate=(
                f'<b>{f["name"]}</b><br>'
                f'Members: {f["size"]}<br>'
                f'Rep: {f["reputation"]:+d}<br>'
                f'Techs: {", ".join(f["techs"]) or "none"}'
                '<extra></extra>'
            ),
        ))

    return fig


# ══════════════════════════════════════════════════════════════════════════
# Reputation time-series figure
# ══════════════════════════════════════════════════════════════════════════

def build_rep_chart(data: dict) -> go.Figure:
    """Line chart: reputation score over time for the top 5 factions."""
    history   = data.get('rep_history', [])
    factions  = data.get('factions',    [])
    top5      = [f['name'] for f in factions[:5]]

    fig = go.Figure()
    for idx, name in enumerate(top5):
        color  = _FACTION_COLORS[idx % len(_FACTION_COLORS)]
        ticks_ = [h['tick']                  for h in history if name in h['reputations']]
        reps_  = [h['reputations'][name]     for h in history if name in h['reputations']]
        if not ticks_:
            continue
        fig.add_trace(go.Scatter(
            x=ticks_, y=reps_,
            mode='lines',
            line=dict(color=color, width=2),
            name=name,
            hovertemplate=f'<b>{name}</b>: %{{y:+d}}<br>Tick %{{x}}<extra></extra>',
        ))

    # Allied / Reviled reference bands
    for y_val, label, color in [( 5, 'Allied',  '#44ff88'), (-5, 'Reviled', '#ff4444')]:
        fig.add_hline(
            y=y_val,
            line_dash='dot',
            line_color=color,
            opacity=0.6,
            annotation_text=f'  {label}',
            annotation_position='right',
            annotation_font_color=color,
            annotation_font_size=11,
        )

    fig.update_layout(
        title=dict(text='Faction Reputation Over Time',
                   font=dict(color='#dddddd', size=13), x=0.0),
        paper_bgcolor='#0e1117',
        plot_bgcolor='#111827',
        font=dict(color='white'),
        xaxis=dict(
            title='Tick',
            gridcolor='#1e2233',
            zeroline=False,
            tickfont=dict(size=10),
        ),
        yaxis=dict(
            title='Reputation',
            range=[-11, 11],
            gridcolor='#1e2233',
            zeroline=True,
            zerolinecolor='#444455',
            tickvals=list(range(-10, 11, 2)),
        ),
        legend=dict(bgcolor='rgba(0,0,0,0)', font=dict(color='white', size=11)),
        margin=dict(l=50, r=60, t=40, b=50),
        height=320,
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════
# Page config — must be first Streamlit call
# ══════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title='AI Sandbox — Live Dashboard',
    page_icon='🗺',
    layout='wide',
    initial_sidebar_state='expanded',
)

# Inject minimal dark-mode polish
st.markdown("""
<style>
[data-testid="stTextArea"] textarea {
    font-family: 'Courier New', monospace;
    font-size: 11px;
    background: #0a0e17;
    color: #a8c8a8;
    border: 1px solid #2a3040;
}
[data-testid="metric-container"] {
    background: #111827;
    border-radius: 8px;
    padding: 10px 16px;
    margin-bottom: 6px;
}
</style>
""", unsafe_allow_html=True)

# ── Auto-refresh: 500 ms = 2 FPS ─────────────────────────────────────────
if _HAS_AUTOREFRESH:
    _st_autorefresh(interval=500, key='sim_autorefresh')

# ── Load data ─────────────────────────────────────────────────────────────
data = load_data()

# ══════════════════════════════════════════════════════════════════════════
# Sidebar
# ══════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.title('🗺 AI Sandbox')
    st.caption('Civilization Simulation · Live Monitor')

    if not _HAS_AUTOREFRESH:
        if st.button('⟳  Refresh', use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.caption('Auto-refresh unavailable.\n`pip install streamlit-autorefresh`')

    st.divider()

    if data is None:
        st.warning(
            '**Waiting for simulation data…**\n\n'
            'Run the simulation first:\n\n```\npython sim.py\n```\n\n'
            'The dashboard file is written every 25 ticks.'
        )
    else:
        tick      = data['tick']
        alive     = data['alive']
        tick_rate = data['tick_rate']
        max_gen   = data['max_gen']
        factions_ = data.get('factions', [])

        st.metric('⏱  Tick',            f'{tick:,}')
        st.metric('👥 Alive',            str(alive))
        st.metric('⚡ Tick Rate',        f'{tick_rate:.2f} t/s')
        st.metric('🧬 Max Generation',   f'Gen {max_gen}')
        st.metric('🏛  Active Factions', str(len(factions_)))

        st.divider()
        st.subheader('Factions')
        for idx, f in enumerate(factions_[:8]):
            rep    = f['reputation']
            rep_ic = '🤝' if rep >= 5 else ('😤' if rep <= -5 else '😐')
            sett   = '🏰' if f.get('settled') else '🏕'
            color  = _FACTION_COLORS[idx % len(_FACTION_COLORS)]
            st.markdown(
                f'<span style="color:{color}">●</span> '
                f'**{f["name"]}** {sett}  \n'
                f'&nbsp;&nbsp;&nbsp;{f["size"]} members · rep {rep:+d} {rep_ic}',
                unsafe_allow_html=True,
            )

        st.divider()
        st.subheader('Biome Legend')
        for i, name in enumerate(_BIOME_NAMES):
            st.markdown(
                f'<span style="background:{_BIOME_HEX[i]};padding:2px 8px;'
                f'border-radius:3px;font-size:12px;color:#fff">{name}</span>',
                unsafe_allow_html=True,
            )

# ══════════════════════════════════════════════════════════════════════════
# Main panel
# ══════════════════════════════════════════════════════════════════════════

if data is None:
    st.info(
        '**dashboard_data.json** not found yet.  \n'
        'Start the simulation (`python sim.py`) and the first snapshot '
        'appears after tick 25.'
    )
    st.stop()

# Header bar
tick_rate_str = f'{data["tick_rate"]:.2f} t/s'
st.markdown(
    f'### Tick **{data["tick"]:,}** &nbsp;·&nbsp; '
    f'{data["alive"]} alive &nbsp;·&nbsp; '
    f'{tick_rate_str} &nbsp;·&nbsp; '
    f'Gen {data["max_gen"]}',
    unsafe_allow_html=True,
)

col_map, col_right = st.columns([3, 2], gap='medium')

with col_map:
    st.subheader('World Map')
    st.plotly_chart(
        build_world_map(data),
        use_container_width=True,
        key='world_map',
        config={'displayModeBar': False},
    )

with col_right:
    st.plotly_chart(
        build_rep_chart(data),
        use_container_width=True,
        key='rep_chart',
        config={'displayModeBar': False},
    )

    st.subheader('Event Feed')
    events    = list(reversed(data.get('event_tail', [])))
    event_txt = '\n'.join(events[:30])
    st.text_area(
        label='Events',
        value=event_txt,
        height=215,
        disabled=True,
        key='event_feed',
        label_visibility='collapsed',
    )
