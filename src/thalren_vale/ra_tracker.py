"""
ra_tracker.py — Reverse Assimilation instrumentation.

Implements per-tick, per-faction 27-dimensional belief-composition logging,
annexation-event snapshots, and post-annexation cosine-similarity tracking
at t+50, t+100, t+200.  All output is gated behind config.BELIEF_TRACKING_ENABLED.

CSV outputs (written to data/):
  ra_faction_beliefs_{condition}_seed_{seed}.csv
  ra_annexations_{condition}_seed_{seed}.csv
  ra_followups_{condition}_seed_{seed}.csv
"""

import csv
import math
import os
from pathlib import Path

from .beliefs import LABELS, inh_cores

# Canonical ordered list of the 27 belief keys (stable column order)
BELIEF_KEYS = sorted(LABELS.keys())


# ── Helpers ─────────────────────────────────────────────────────────────

def faction_belief_vector(faction) -> dict[str, int]:
    """Return {belief_key: count} across all members of *faction*."""
    vec = {k: 0 for k in BELIEF_KEYS}
    for m in faction.members:
        for core in inh_cores(m):
            if core in vec:
                vec[core] += 1
    return vec


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity between two belief-count dicts."""
    dot = sum(a[k] * b[k] for k in BELIEF_KEYS)
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ── Tracker class ───────────────────────────────────────────────────────

class RATracker:
    """Reverse Assimilation instrumentation tracker.

    Instantiated once per run when --enable-belief-tracking is passed.
    """

    # Post-annexation follow-up offsets (ticks after annexation)
    FOLLOWUP_OFFSETS = (50, 100, 200)

    def __init__(self, seed: int, condition: str, output_dir: str = "data"):
        self.seed = seed
        self.condition = condition
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # ── Faction-level belief composition CSV ────────────────────────
        comp_path = os.path.join(
            output_dir, f"ra_faction_beliefs_{condition}_seed_{seed}.csv")
        self._comp_fh = open(comp_path, 'w', newline='', encoding='utf-8')
        self._comp_w = csv.writer(self._comp_fh)
        self._comp_w.writerow(
            ['seed', 'tick', 'faction', 'member_count'] + BELIEF_KEYS)
        self._comp_fh.flush()

        # ── Annexation / voluntary-merge snapshot CSV ───────────────────
        annex_path = os.path.join(
            output_dir, f"ra_annexations_{condition}_seed_{seed}.csv")
        self._annex_fh = open(annex_path, 'w', newline='', encoding='utf-8')
        self._annex_w = csv.writer(self._annex_fh)
        self._annex_w.writerow(
            ['seed', 'tick', 'merge_type', 'host_faction', 'absorbed_faction',
             'host_members', 'absorbed_members']
            + [f'host_{k}' for k in BELIEF_KEYS]
            + [f'absorbed_{k}' for k in BELIEF_KEYS])
        self._annex_fh.flush()

        # ── Post-annexation follow-up CSV ───────────────────────────────
        followup_path = os.path.join(
            output_dir, f"ra_followups_{condition}_seed_{seed}.csv")
        self._follow_fh = open(followup_path, 'w', newline='', encoding='utf-8')
        self._follow_w = csv.writer(self._follow_fh)
        self._follow_w.writerow(
            ['seed', 'annexation_tick', 'followup_tick', 'offset',
             'merge_type', 'host_faction', 'absorbed_faction',
             'host_members_now', 'cosine_vs_absorbed_pre',
             'cosine_vs_host_pre']
            + [f'host_now_{k}' for k in BELIEF_KEYS])
        self._follow_fh.flush()

        # Pending follow-ups: list of dicts
        #   {tick_due, merge_type, host_name, absorbed_name,
        #    absorbed_pre_vec, host_pre_vec, annexation_tick, offset}
        self._pending_followups: list[dict] = []

    # ────────────────────────────────────────────────────────────────────
    # Per-tick: faction compositions (called every tick from sim.py)
    # ────────────────────────────────────────────────────────────────────

    def record_faction_compositions(self, tick: int, factions) -> None:
        """Write one row per active faction with its 27-dim belief vector."""
        for f in factions:
            if not f.members:
                continue
            vec = faction_belief_vector(f)
            self._comp_w.writerow(
                [self.seed, tick, f.name, len(f.members)]
                + [vec[k] for k in BELIEF_KEYS])
        if tick % 10 == 0:
            self._comp_fh.flush()

    # ────────────────────────────────────────────────────────────────────
    # Annexation / voluntary-merge snapshot
    # ────────────────────────────────────────────────────────────────────

    def record_annexation(self, tick: int, host_faction, absorbed_faction,
                          merge_type: str = 'annexation') -> None:
        """Snapshot belief compositions of both factions BEFORE members move.

        Must be called while absorbed_faction.members is still populated.
        *merge_type* is 'annexation' or 'voluntary_merge'.
        """
        host_vec = faction_belief_vector(host_faction)
        absorbed_vec = faction_belief_vector(absorbed_faction)

        self._annex_w.writerow(
            [self.seed, tick, merge_type,
             host_faction.name, absorbed_faction.name,
             len(host_faction.members), len(absorbed_faction.members)]
            + [host_vec[k] for k in BELIEF_KEYS]
            + [absorbed_vec[k] for k in BELIEF_KEYS])
        self._annex_fh.flush()

        # Schedule follow-up measurements
        for offset in self.FOLLOWUP_OFFSETS:
            self._pending_followups.append({
                'tick_due': tick + offset,
                'offset': offset,
                'merge_type': merge_type,
                'host_name': host_faction.name,
                'absorbed_name': absorbed_faction.name,
                'absorbed_pre_vec': dict(absorbed_vec),
                'host_pre_vec': dict(host_vec),
                'annexation_tick': tick,
            })

    # ────────────────────────────────────────────────────────────────────
    # Post-annexation follow-up (called every tick from sim.py)
    # ────────────────────────────────────────────────────────────────────

    def check_followups(self, tick: int, factions) -> None:
        """Check if any scheduled follow-up measurements are due."""
        if not self._pending_followups:
            return

        factions_by_name = {f.name: f for f in factions if f.members}
        still_pending = []

        for fu in self._pending_followups:
            if tick < fu['tick_due']:
                still_pending.append(fu)
                continue

            # Due now — measure the host faction's current composition
            host = factions_by_name.get(fu['host_name'])
            if host is None:
                # Host faction dissolved; record as missing
                self._follow_w.writerow(
                    [self.seed, fu['annexation_tick'], tick, fu['offset'],
                     fu['merge_type'], fu['host_name'], fu['absorbed_name'],
                     0, '', '']
                    + [''] * len(BELIEF_KEYS))
                continue

            host_now_vec = faction_belief_vector(host)
            cos_vs_absorbed = _cosine_similarity(
                host_now_vec, fu['absorbed_pre_vec'])
            cos_vs_host_pre = _cosine_similarity(
                host_now_vec, fu['host_pre_vec'])

            self._follow_w.writerow(
                [self.seed, fu['annexation_tick'], tick, fu['offset'],
                 fu['merge_type'], fu['host_name'], fu['absorbed_name'],
                 len(host.members),
                 round(cos_vs_absorbed, 6), round(cos_vs_host_pre, 6)]
                + [host_now_vec[k] for k in BELIEF_KEYS])

        self._pending_followups = still_pending
        if tick % 10 == 0:
            self._follow_fh.flush()

    # ────────────────────────────────────────────────────────────────────
    # Cleanup
    # ────────────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Flush and close all CSV file handles."""
        for fh in (self._comp_fh, self._annex_fh, self._follow_fh):
            try:
                fh.flush()
                fh.close()
            except Exception:
                pass
