# (c) 2026 (KriaetvAspie / AspieTheBard)
# Licensed under the Polyform Noncommercial License 1.0.0
"""
metrics.py — Per-Tick Metrics Logger for Thalren Vale simulation.

Collects per-tick simulation metrics, discrete events, and belief snapshots,
writing them to CSV files for academic analysis.
"""

import csv
import os
import time
import tracemalloc
from pathlib import Path


class MetricsLogger:
    """Collects per-tick simulation metrics and writes them to CSV."""

    # BASE_PRICES for wealth calculation (matching economy.py)
    _BASE_PRICES = {'food': 2, 'wood': 3, 'ore': 5, 'stone': 4}
    _RES_TRADE = ['food', 'wood', 'ore', 'stone']

    # Season constants (matching inhabitants.py)
    _WINTER_START = 25
    _WINTER_LEN = 8
    _CYCLE_LEN = 50

    def __init__(self, seed: int, condition: str, output_dir: str = "data"):
        self.seed = seed
        self.condition = condition
        self.output_dir = output_dir

        # Create output directory
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Open CSV files
        self._metrics_path = os.path.join(output_dir, f"metrics_seed_{seed}.csv")
        self._events_path = os.path.join(output_dir, f"faction_events_seed_{seed}.csv")
        self._beliefs_path = os.path.join(output_dir, f"beliefs_seed_{seed}.csv")

        self._metrics_fh = open(self._metrics_path, 'w', newline='', encoding='utf-8')
        self._events_fh = open(self._events_path, 'w', newline='', encoding='utf-8')
        self._beliefs_fh = open(self._beliefs_path, 'w', newline='', encoding='utf-8')

        self._metrics_writer = csv.writer(self._metrics_fh)
        self._events_writer = csv.writer(self._events_fh)
        self._beliefs_writer = csv.writer(self._beliefs_fh)

        # Write header rows
        self._metrics_writer.writerow([
            'seed', 'tick', 'population', 'faction_count', 'war_count',
            'total_wars_declared', 'total_deaths', 'total_births',
            'gini', 'mean_trust', 'mean_food', 'total_techs',
            'total_treaties', 'max_generation', 'mean_generation',
            'largest_faction_size', 'smallest_faction_size',
            'total_schisms', 'total_mergers', 'peace_ticks',
            'mean_reputation', 'reputation_variance',
            'grid_size', 'season',
        ])
        self._metrics_fh.flush()

        self._events_writer.writerow([
            'seed', 'tick', 'event_type', 'actor', 'target', 'detail',
        ])
        self._events_fh.flush()

        self._beliefs_writer.writerow([
            'seed', 'tick', 'inhabitant_id', 'faction', 'beliefs',
        ])
        self._beliefs_fh.flush()

        # Cumulative counters
        self.total_wars_declared = 0
        self.total_deaths = 0
        self.total_births = 0
        self.total_schisms = 0
        self.total_mergers = 0
        self.total_treaties_formed = 0
        self.total_treaties_broken = 0
        self.stagnation_events = 0
        self.era_count = 0

        # Running stats for finalize
        self._peak_population = 0
        self._min_population = float('inf')
        self._peak_faction_count = 0
        self._total_factions_formed = 0
        self._first_faction_tick = None
        self._gini_values = []
        self._unique_techs = set()
        self._war_starts = {}    # war_id → start_tick
        self._war_durations = []

        # Wall clock and memory tracking
        self.start_time = time.time()
        tracemalloc.start()

    # ──────────────────────────────────────────────────────────────────────
    # Wealth and Gini helpers (matching economy.py formulas)
    # ──────────────────────────────────────────────────────────────────────

    def _inhabitant_wealth(self, inh) -> float:
        """Compute wealth of an inhabitant (matching economy.py formula)."""
        try:
            inv = getattr(inh, 'inventory', {})
            inv_wealth = sum(
                inv.get(k, 0) * self._BASE_PRICES.get(k, 1)
                for k in self._RES_TRADE
            )
            return inv_wealth + getattr(inh, 'currency', 0)
        except Exception:
            return 0.0

    def _compute_gini(self, inhabitants) -> float:
        """Compute Gini coefficient (identical to economy.gini_coefficient)."""
        try:
            vals = sorted(max(0.0, self._inhabitant_wealth(p)) for p in inhabitants)
            n = len(vals)
            if n == 0:
                return 0.0
            total = sum(vals)
            if total == 0:
                return 0.0
            cum = sum((2 * (i + 1) - n - 1) * v for i, v in enumerate(vals))
            return round(cum / (n * total), 4)
        except Exception:
            return 0.0

    def _compute_mean_trust(self, inhabitants) -> float:
        """Mean of ALL pairwise trust values across all living inhabitants."""
        try:
            all_trust = []
            for inh in inhabitants:
                trust_dict = getattr(inh, 'trust', None)
                if trust_dict:
                    all_trust.extend(trust_dict.values())
            if not all_trust:
                return 0.0
            return round(sum(all_trust) / len(all_trust), 4)
        except Exception:
            return 0.0

    def _get_reputations(self, factions) -> list:
        """Fetch reputation values for active factions via diplomacy module."""
        reps = []
        try:
            from . import diplomacy as _dip
            for f in factions:
                reps.append(_dip.get_rep(f.name))
        except Exception:
            reps = [0] * len(factions)
        return reps

    # ──────────────────────────────────────────────────────────────────────
    # Per-tick recording
    # ──────────────────────────────────────────────────────────────────────

    def record_tick(self, tick, world, inhabitants, factions,
                    wars, treaties, peace_ticks):
        """Called once per tick from the main loop.  Writes one CSV row."""
        try:
            pop = len(inhabitants)
            active_factions = [f for f in factions if f.members]
            faction_count = len(active_factions)
            war_count = len(wars) if wars else 0

            gini = self._compute_gini(inhabitants)
            mean_trust = self._compute_mean_trust(inhabitants)

            # Mean food
            mean_food = 0.0
            if inhabitants:
                total_food = sum(
                    getattr(p, 'inventory', {}).get('food', 0)
                    for p in inhabitants
                )
                mean_food = round(total_food / len(inhabitants), 4)

            # Total techs (count duplicates across factions)
            total_techs = sum(
                len(getattr(f, 'techs', set())) for f in active_factions
            )

            # Treaty count
            total_treaties = len(treaties) if treaties else 0

            # Generation stats
            max_gen = max(
                (getattr(p, 'generation', 0) for p in inhabitants),
                default=0,
            )
            mean_gen = 0.0
            if inhabitants:
                gen_sum = sum(getattr(p, 'generation', 0) for p in inhabitants)
                mean_gen = round(gen_sum / len(inhabitants), 4)

            # Faction size extremes
            largest_size = max(
                (len(f.members) for f in active_factions),
                default=0,
            )
            smallest_size = min(
                (len(f.members) for f in active_factions if f.members),
                default=0,
            )

            # Reputation stats
            mean_rep = 0.0
            rep_var = 0.0
            if active_factions:
                reps = self._get_reputations(active_factions)
                if reps:
                    mean_rep = round(sum(reps) / len(reps), 4)
                    rep_var = round(
                        sum((r - mean_rep) ** 2 for r in reps) / len(reps), 4
                    )

            # Grid size
            grid_size = len(world) if world else 0

            # Season (1 = winter, 0 = otherwise)
            phase = (tick - 1) % self._CYCLE_LEN
            season = 1 if (self._WINTER_START
                           <= phase
                           < self._WINTER_START + self._WINTER_LEN) else 0

            # Update running stats
            self._peak_population = max(self._peak_population, pop)
            if pop > 0:
                self._min_population = min(self._min_population, pop)
            self._peak_faction_count = max(self._peak_faction_count, faction_count)
            self._gini_values.append(gini)

            self._metrics_writer.writerow([
                self.seed, tick, pop, faction_count, war_count,
                self.total_wars_declared, self.total_deaths, self.total_births,
                gini, mean_trust, mean_food, total_techs,
                total_treaties, max_gen, mean_gen,
                largest_size, smallest_size,
                self.total_schisms, self.total_mergers, peace_ticks,
                mean_rep, rep_var,
                grid_size, season,
            ])

            # Flush every 100 ticks
            if tick % 100 == 0:
                self._metrics_fh.flush()

        except Exception:
            pass  # Never crash the simulation

    # ──────────────────────────────────────────────────────────────────────
    # Discrete event recording
    # ──────────────────────────────────────────────────────────────────────

    def record_event(self, tick, event_type, actor="", target="", detail=""):
        """Called whenever a discrete event occurs.  Writes one CSV row
        and increments cumulative counters.

        event_type must be one of:
            'war_declared', 'war_ended', 'faction_formed', 'faction_dissolved',
            'schism', 'merger', 'treaty_signed', 'treaty_broken',
            'tech_researched', 'settlement_founded', 'birth', 'death',
            'era_shift', 'stagnation_trigger', 'raid', 'world_event'
        """
        try:
            self._events_writer.writerow([
                self.seed, tick, event_type, actor, target, detail,
            ])
            self._events_fh.flush()

            # Increment cumulative counters
            if event_type == 'war_declared':
                self.total_wars_declared += 1
                war_id = f"{actor}_{target}_{tick}"
                self._war_starts[war_id] = tick
            elif event_type == 'war_ended':
                # Match a war start to compute duration
                matched = None
                for wid, start_t in list(self._war_starts.items()):
                    if ((actor and actor in wid) or (target and target in wid)):
                        self._war_durations.append(tick - start_t)
                        matched = wid
                        break
                if matched:
                    del self._war_starts[matched]
            elif event_type == 'death':
                self.total_deaths += 1
            elif event_type == 'birth':
                self.total_births += 1
            elif event_type == 'schism':
                self.total_schisms += 1
            elif event_type == 'merger':
                self.total_mergers += 1
            elif event_type == 'treaty_signed':
                self.total_treaties_formed += 1
            elif event_type == 'treaty_broken':
                self.total_treaties_broken += 1
            elif event_type == 'faction_formed':
                self._total_factions_formed += 1
                if self._first_faction_tick is None:
                    self._first_faction_tick = tick
            elif event_type == 'tech_researched':
                if detail:
                    self._unique_techs.add(detail)
            elif event_type == 'stagnation_trigger':
                self.stagnation_events += 1
            elif event_type == 'era_shift':
                self.era_count += 1

        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────────
    # Belief snapshots (every 100 ticks)
    # ──────────────────────────────────────────────────────────────────────

    def record_beliefs(self, tick, inhabitants, factions):
        """Called every 100 ticks.  Writes one row per living inhabitant."""
        try:
            for inh in inhabitants:
                faction_name = getattr(inh, 'faction', None) or 'none'
                beliefs_list = getattr(inh, 'beliefs', [])
                beliefs_str = ';'.join(sorted(beliefs_list)) if beliefs_list else ''
                self._beliefs_writer.writerow([
                    self.seed, tick,
                    getattr(inh, 'name', ''),
                    faction_name,
                    beliefs_str,
                ])
            self._beliefs_fh.flush()
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────────
    # Finalize — run-level summary
    # ──────────────────────────────────────────────────────────────────────

    def finalize(self, world, inhabitants, factions):
        """Called once at end of simulation.  Appends one row to
        data/run_summaries.csv.
        """
        try:
            wall_clock = round(time.time() - self.start_time, 2)

            try:
                peak_ram = round(
                    tracemalloc.get_traced_memory()[1] / (1024 * 1024), 2
                )
            except Exception:
                peak_ram = 0.0

            try:
                tracemalloc.stop()
            except Exception:
                pass

            final_pop = len(inhabitants)
            if self._min_population == float('inf'):
                self._min_population = final_pop

            active = [f for f in factions if f.members]
            final_faction_count = len(active)

            # Gini summaries
            mean_gini = 0.0
            peak_gini = 0.0
            final_gini = 0.0
            if self._gini_values:
                mean_gini = round(
                    sum(self._gini_values) / len(self._gini_values), 4
                )
                peak_gini = round(max(self._gini_values), 4)
                final_gini = round(self._gini_values[-1], 4)

            # Tech stats
            total_unique_techs = len(self._unique_techs)
            mean_tech_per_faction = 0.0
            if active:
                tech_counts = [len(getattr(f, 'techs', set())) for f in active]
                mean_tech_per_faction = round(
                    sum(tech_counts) / len(tech_counts), 4
                )

            # Generation
            max_gen = max(
                (getattr(p, 'generation', 0) for p in inhabitants),
                default=0,
            )

            # War duration
            mean_war_dur = 0.0
            if self._war_durations:
                mean_war_dur = round(
                    sum(self._war_durations) / len(self._war_durations), 4
                )

            summary_path = os.path.join(self.output_dir, "run_summaries.csv")
            file_exists = os.path.isfile(summary_path)
            with open(summary_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow([
                        'seed', 'condition', 'final_population',
                        'peak_population', 'min_population',
                        'total_factions_formed', 'final_faction_count',
                        'peak_faction_count', 'first_faction_tick',
                        'total_wars', 'total_deaths', 'total_births',
                        'total_schisms', 'total_mergers',
                        'mean_gini', 'final_gini', 'peak_gini',
                        'total_unique_techs', 'mean_tech_count_per_faction',
                        'total_treaties_formed', 'total_treaties_broken',
                        'max_generation', 'mean_war_duration',
                        'stagnation_events', 'era_count',
                        'wall_clock_seconds', 'peak_ram_mb',
                    ])
                writer.writerow([
                    self.seed, self.condition, final_pop,
                    self._peak_population, self._min_population,
                    self._total_factions_formed, final_faction_count,
                    self._peak_faction_count,
                    (self._first_faction_tick
                     if self._first_faction_tick is not None else 0),
                    self.total_wars_declared, self.total_deaths,
                    self.total_births,
                    self.total_schisms, self.total_mergers,
                    mean_gini, final_gini, peak_gini,
                    total_unique_techs, mean_tech_per_faction,
                    self.total_treaties_formed, self.total_treaties_broken,
                    max_gen, mean_war_dur,
                    self.stagnation_events, self.era_count,
                    wall_clock, peak_ram,
                ])

        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────────
    # Cleanup
    # ──────────────────────────────────────────────────────────────────────

    def close(self):
        """Flush and close all CSV file handles.  Call after finalize()."""
        for fh in (self._metrics_fh, self._events_fh, self._beliefs_fh):
            try:
                fh.flush()
                fh.close()
            except Exception:
                pass
