"""
dashboard_bridge.py — Periodic JSON snapshot writer for the Streamlit live dashboard.

Call write_dashboard_snapshot() from sim.py every DASHBOARD_WRITE_EVERY ticks.
Uses an atomic rename-swap so the dashboard process never reads a half-written file.

No Streamlit dependency — this runs inside the main simulation process.
"""

import json
import os
import collections
import pathlib

from .world     import world, BIOME_ID
from .diplomacy import _reputation as _rep_store

# ── Configuration ─────────────────────────────────────────────────────────
DASHBOARD_WRITE_EVERY: int    = 25                          # write interval (ticks)
DASHBOARD_DATA_PATH:   pathlib.Path = pathlib.Path("dashboard_data.json")

_REP_HISTORY_MAX = 120   # keep last 120 snapshots → 3 000 ticks of history at interval=25

# ── Generation-suffix → int mapping ───────────────────────────────────────
_GEN_SUFFIX_MAP: dict[str, int] = {
    'II': 2, 'III': 3, 'IV': 4, 'V': 5,
    'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10,
}

# ── Rolling reputation history (module-level, survives across calls) ───────
_rep_history: collections.deque = collections.deque(maxlen=_REP_HISTORY_MAX)


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────

def _max_generation(people: list) -> int:
    """Return the highest observed generation number from inhabitant name suffixes."""
    hi = 1
    for p in people:
        parts = p.name.rsplit(' ', 1)
        if len(parts) == 2:
            hi = max(hi, _GEN_SUFFIX_MAP.get(parts[1], 1))
    return hi


def _tick_rate(tick_times: list) -> float:
    """Ticks per second averaged over the last 30 recorded tick durations."""
    if not tick_times:
        return 0.0
    recent = tick_times[-30:]
    total  = sum(recent)
    return round(len(recent) / total, 2) if total > 0 else 0.0


# ──────────────────────────────────────────────────────────────────────────
# Main API
# ──────────────────────────────────────────────────────────────────────────

def write_dashboard_snapshot(
    t:          int,
    people:     list,
    factions:   list,
    tick_times: list,
    event_log:  list,
) -> None:
    """Serialise current simulation state and write to DASHBOARD_DATA_PATH atomically.

    The write goes to a .tmp file first; os.replace() then performs an atomic rename
    so the dashboard reader never sees a partial JSON file.
    """
    rows = len(world)
    cols = len(world[0]) if rows else 0

    # ── Biome grid: 2-D list of biome_id ints ─────────────────────────────
    biome_grid = [
        [world[r][c]['biome_id'] for c in range(cols)]
        for r in range(rows)
    ]

    # ── Faction snapshots ─────────────────────────────────────────────────
    rep_snap: dict[str, int] = {}
    faction_data: list = []
    for f in factions:
        if not f.members:
            continue
        rep = _rep_store.get(f.name, 0)
        rep_snap[f.name] = rep
        faction_data.append({
            'name':       f.name,
            'size':       len(f.members),
            'reputation': rep,
            'techs':      sorted(getattr(f, 'techs', set())),
            'members':    [[m.r, m.c] for m in f.members],
            'settled':    bool(getattr(f, 'is_settled', False)),
        })
    faction_data.sort(key=lambda x: x['size'], reverse=True)

    # ── Append reputation snapshot to rolling history ─────────────────────
    _rep_history.append({'tick': t, 'reputations': rep_snap})

    # ── Assemble snapshot ─────────────────────────────────────────────────
    snap = {
        'tick':        t,
        'alive':       len(people),
        'tick_rate':   _tick_rate(tick_times),
        'max_gen':     _max_generation(people),
        'factions':    faction_data,
        'biome_grid':  biome_grid,
        'rep_history': list(_rep_history),
        'event_tail':  event_log[-40:],     # last 40 events for the live feed
    }

    # ── Atomic write ──────────────────────────────────────────────────────
    tmp = DASHBOARD_DATA_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(snap, separators=(',', ':')), encoding='utf-8')
    os.replace(tmp, DASHBOARD_DATA_PATH)
