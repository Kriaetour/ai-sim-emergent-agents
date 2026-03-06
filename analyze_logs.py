"""
analyze_logs.py — Per-run event analysis for Thalren Vale log files.

Run from any directory:
    python analyze_logs.py --log-dir ./logs

Outputs:
    run_event_summary.csv   — one row per run, all event counts and derived metrics
    analysis_report.txt     — human-readable narrative summary of the ensemble

What this extracts (complementing results.csv):
    Wars:          declarations, resolutions, democratic overrides
    Disruptions:   plague, migration, civil war, promised land, prophet
    Factions:      formations, schisms, mergers, extinctions
    Technology:    discoveries per branch, first-discovery ticks
    Diplomacy:     treaties formed, treaties broken
    Population:    births, starvation deaths, battle deaths
    Eras:          era shift count, era type distribution
    Religion:      religions founded, holy wars, temples built

FIXES vs original:
    - Technology branch detection is now STATEFUL: a TECH DISCOVERED line sets a
      flag; the very next non-empty line is checked for "Branch: <name>".  This
      correctly handles the two-line log format where branch appears on its own line.
    - Tech branch names corrected to match technology.py: Weaponry/Metalwork/
      Scavenging/Masonry/Steel for Martial; Farming/Sailing/Mining/Engineering/
      Currency/Tools for Industrial; Writing/Oral Tradition/Medicine/Code of Laws
      for Civic.
    - Treaty pattern broadened: matches on treaty TYPE names (NON-AGGRESSION,
      TRADE AGREEMENT, MUTUAL DEFENSE, TRIBUTE PACT) with "sign" suffix, as well
      as the original TREATY keyword variants, to handle whichever format the
      simulation writes.
    - Democratic override pattern tightened: no longer fires on the peace-
      escalation INCIDENT keyword (a different mechanism). Now requires explicit
      "override" or "bypassed" language, or the "DEMOCRATIC FAILURE" string if
      sim.py ever writes that.
    - first_writing_tick now derived from the corrected tech_civic counter rather
      than the previously-broken tech_knowledge counter.
    - Reverse Assimilation (formerly Cultural Inversion) counter added.
"""

import argparse
import csv
import glob
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, stdev


# ── Shared tick extractor ─────────────────────────────────────────────────────
TICK_RE = re.compile(r'Tick\s+(\d+)')

# Matches the [t=NN] format used by religion.py / Layer 9 events.
_T_EQ_RE = re.compile(r'\[t=(\d+)\]')


def _p(*keywords, flags=re.I):
    """Return a compiled pattern matching any keyword, requiring a preceding Tick token.

    This ensures only lines that are part of the tick-by-tick event log are
    counted, filtering out header text, summary blocks, etc.
    """
    alt = '|'.join(rf'(?:Tick[^\n]*?{kw})' for kw in keywords)
    return re.compile(f'(?:{alt})', flags)


def _free(*keywords, flags=re.I):
    """Return a compiled pattern matching any keyword WITHOUT requiring a Tick prefix.

    Use this for multi-line constructs (e.g. branch lines) that appear on their
    own line immediately after a tick-prefixed line.
    """
    alt = '|'.join(re.escape(kw) if not any(c in kw for c in r'\.^$*+?{}[]|()')
                   else kw for kw in keywords)
    return re.compile(f'(?:{alt})', flags)


# ── Event patterns ────────────────────────────────────────────────────────────

PATTERNS = {
    # ── Wars ──────────────────────────────────────────────────────────────────
    # Exclude "HOLY WAR declared" lines — those are counted separately as holy_war.
    'war_declared':        re.compile(
        r'Tick[^\n]*?(?<!HOLY\s)WAR DECLARED',
        re.IGNORECASE,
    ),
    'war_ends':            _p('WAR ENDS', 'PEACE TREATY', 'CEASEFIRE', 'SURRENDER'),

    # ── Disruption events ─────────────────────────────────────────────────────
    'plague':              _p('PLAGUE SWEEPS', 'PLAGUE STRIKES'),
    'great_migration':     _p('GREAT MIGRATION'),
    'civil_war':           _p('CIVIL WAR'),
    'promised_land':       _p('PROMISED LAND'),
    'prophet':             _p('PROPHET arrives', 'PROPHET cries', 'A PROPHET'),

    # ── Faction lifecycle ─────────────────────────────────────────────────────
    'faction_formed':      _p('FACTION FORMED', r'FACTION\s*[—–\-]+[^\n]*?formed'),
    'schism':              _p('SCHISM'),
    'faction_merge':       _p('FACTION MERGE', 'DIPLOMATIC MERGE', 'MERGER'),

    # ── Technology (total count only; branch breakdown is stateful — see below) ──
    'tech_discovered':     _p('TECH DISCOVERED'),

    # ── Diplomacy ─────────────────────────────────────────────────────────────
    # FIX: broadened to match treaty TYPE names without requiring "TREATY" on same
    # line. Covers both "TREATY SIGNED" and "NON-AGGRESSION PACT sign" formats.
    'treaty_formed': re.compile(
        r'Tick[^\n]*?(?:'
        r'TREATY[^\n]*?(?:sign|formed|agreed|established)'
        r'|NON.AGGRESSION[^\n]*?sign'
        r'|TRADE AGREEMENT[^\n]*?sign'
        r'|MUTUAL DEFENSE[^\n]*?sign'
        r'|TRIBUTE PACT[^\n]*?sign'
        r'|PACT[^\n]*?sign'
        r')',
        re.IGNORECASE,
    ),
    'treaty_broken':       _p('broke treaty', r'treaty[^\n]*?broken', 'TREATY VIOLATED'),

    # ── Population events ─────────────────────────────────────────────────────
    'birth':               _p('BIRTH'),
    'death_starvation':    _p('starved', 'wasted away', 'died of hunger'),
    'death_battle':        _p('fell in battle', 'slain in battle', 'killed in battle'),

    # ── Era shifts ────────────────────────────────────────────────────────────
    'era_shift':           _p('NEW ERA DAWNS', 'ERA SUMMARY'),

    # ── World events ─────────────────────────────────────────────────────────
    'world_event':         _p('WORLD EVENT'),

    # ── Religion (Layer 9) ───────────────────────────────────────────────────
    'religion_founded':    _p('RELIGION FOUNDED', 'FAITH ESTABLISHED', 'RELIGION EMERGES'),
    'holy_war':            _p('HOLY WAR'),
    'temple_built':        _p('TEMPLE BUILT', 'TEMPLE CONSTRUCTED'),

}

# ── Branch detection (stateful, tick-free) ────────────────────────────────────
# These match the "Branch: X" line that immediately follows a TECH DISCOVERED line.
# Tech names taken directly from technology.py as documented in the README.

BRANCH_PATTERNS = {
    'tech_industrial': re.compile(r'Branch\s*:\s*Industrial', re.IGNORECASE),
    'tech_martial':    re.compile(r'Branch\s*:\s*Martial',    re.IGNORECASE),
    'tech_civic':      re.compile(r'Branch\s*:\s*Civic',      re.IGNORECASE),
}

# Fallback: if branch line is absent, try to infer from tech name on the
# TECH DISCOVERED line itself. This handles log formats that do put the tech
# name inline. Lists match technology.py exactly.
_INDUSTRIAL_TECHS = re.compile(
    r'(?:Tools|Farming|Sailing|Mining|Engineering|Currency)',
    re.IGNORECASE,
)
_MARTIAL_TECHS = re.compile(
    r'(?:Scavenging|Metalwork|Weaponry|Masonry|Steel)',
    re.IGNORECASE,
)
_CIVIC_TECHS = re.compile(
    r'(?:Oral\s+Tradition|Medicine|Writing|Code\s+of\s+Laws)',
    re.IGNORECASE,
)

# Era type labels produced by _era_name() in sim.py
ERA_LABELS = [
    'The Age of Sickness',
    'The Golden Age',
    'The Age of Ruin',
    'The Crimson Years',
    'The Age of Conflict',
    'The Great Famine',
    'The Age of Iron',
    'The Age of Enlightenment',
    'The Long Peace',
]


def extract_run_id(filepath: str) -> str:
    stem = Path(filepath).stem
    m = re.search(r'(\d{8}_\d{6})', stem)
    if m:
        return m.group(1)
    m = re.search(r'(\d+)', stem)
    return m.group(1) if m else stem


def parse_run(filepath: str) -> dict:
    """Parse a single log file and return a dict of per-run metrics."""
    run_id = extract_run_id(filepath)
    metrics = {
        'run_id':       run_id,
        'filepath':     str(filepath),
        'lines_parsed': 0,
        'parse_errors': 0,
    }

    # Initialise all event counters to 0
    for key in PATTERNS:
        metrics[key] = 0
    for key in BRANCH_PATTERNS:
        metrics[key] = 0

    # Tick lists for derived timing metrics
    first_ticks = {}
    war_declared_ticks = []
    war_ended_ticks = []
    tech_ticks = []

    # Era label counts
    era_counts = {label: 0 for label in ERA_LABELS}

    # ── Stateful branch tracking ──────────────────────────────────────────────
    # When we see a TECH DISCOVERED line, set this flag.  The very next non-empty
    # line is then checked against BRANCH_PATTERNS (and the fallback inline names).
    # This correctly handles the two-line log format:
    #   Tick 200: TECH DISCOVERED — The Wild Rovers unlock Sailing
    #   Branch: Industrial
    _awaiting_branch = False
    _branch_tick = None        # tick of the pending TECH DISCOVERED event

    tech_discovered_pat = PATTERNS['tech_discovered']

    with open(filepath, 'r', encoding='utf-8', errors='replace') as fh:
        for raw_line in fh:
            metrics['lines_parsed'] += 1
            line = raw_line.strip()
            if not line:
                continue

            try:
                # ── Normalise [t=NN] lines to Tick format ──────────────────
                # religion.py emits events as "[t=70] EVENT ..." instead of
                # "Tick 0070: EVENT ...".  Normalise so the Tick-prefixed
                # patterns match them without a slow regex alternation.
                if '[t=' in line and 'Tick' not in line:
                    _t_eq = _T_EQ_RE.search(line)
                    if _t_eq:
                        line = line.replace(
                            _t_eq.group(0),
                            f'Tick {int(_t_eq.group(1)):04d}:')

                # ── Branch resolution (must come BEFORE general pattern matching) ──
                if _awaiting_branch:
                    matched_branch = False
                    for branch_key, branch_pat in BRANCH_PATTERNS.items():
                        if branch_pat.search(line):
                            metrics[branch_key] += 1
                            if branch_key not in first_ticks and _branch_tick is not None:
                                first_ticks[branch_key] = _branch_tick
                            matched_branch = True
                            break

                    if not matched_branch:
                        # Fallback: check if tech name is visible on the TECH
                        # DISCOVERED line itself (some log versions include it inline).
                        # We can't go back to that line, so try inline patterns on
                        # current line too (in case branch and name appear together).
                        if _INDUSTRIAL_TECHS.search(line):
                            metrics['tech_industrial'] += 1
                            if 'tech_industrial' not in first_ticks and _branch_tick is not None:
                                first_ticks['tech_industrial'] = _branch_tick
                        elif _MARTIAL_TECHS.search(line):
                            metrics['tech_martial'] += 1
                            if 'tech_martial' not in first_ticks and _branch_tick is not None:
                                first_ticks['tech_martial'] = _branch_tick
                        elif _CIVIC_TECHS.search(line):
                            metrics['tech_civic'] += 1
                            if 'tech_civic' not in first_ticks and _branch_tick is not None:
                                first_ticks['tech_civic'] = _branch_tick
                        # If none matched, branch is unclassified — we still counted
                        # the tech in 'tech_discovered', so no data is lost.

                    _awaiting_branch = False
                    _branch_tick = None
                    # Fall through — this line may also be a new event.

                # ── General pattern matching ──────────────────────────────────
                # Fast pre-filter: skip lines that cannot possibly contain a
                # Tick-prefixed event.  All _p() patterns require "Tick" on the
                # line (after [t=] normalisation above).
                if 'Tick' in line:
                    for key, pattern in PATTERNS.items():
                        try:
                            m = pattern.search(line)
                            if not m:
                                continue
                            metrics[key] += 1
                            tm = TICK_RE.search(line)
                            if tm is None:
                                continue
                            tick = int(tm.group(1))
                            if key not in first_ticks:
                                first_ticks[key] = tick
                            if key == 'war_declared':
                                war_declared_ticks.append(tick)
                            elif key == 'war_ends':
                                war_ended_ticks.append(tick)
                            elif key == 'tech_discovered':
                                tech_ticks.append(tick)
                                # Arm branch detection for the next non-empty line.
                                # Also attempt inline fallback immediately.
                                if _INDUSTRIAL_TECHS.search(line):
                                    metrics['tech_industrial'] += 1
                                    if 'tech_industrial' not in first_ticks:
                                        first_ticks['tech_industrial'] = tick
                                elif _MARTIAL_TECHS.search(line):
                                    metrics['tech_martial'] += 1
                                    if 'tech_martial' not in first_ticks:
                                        first_ticks['tech_martial'] = tick
                                elif _CIVIC_TECHS.search(line):
                                    metrics['tech_civic'] += 1
                                    if 'tech_civic' not in first_ticks:
                                        first_ticks['tech_civic'] = tick
                                else:
                                    # Name not inline — wait for next line.
                                    _awaiting_branch = True
                                    _branch_tick = tick
                        except Exception:
                            metrics['parse_errors'] += 1

                # ── Era label counting ────────────────────────────────────────
                for label in ERA_LABELS:
                    if label in line:
                        era_counts[label] += 1

            except Exception:
                metrics['parse_errors'] += 1

    # ── Derived metrics ───────────────────────────────────────────────────────

    declared = metrics['war_declared']
    ended    = metrics['war_ends']
    metrics['war_resolution_rate'] = round(ended / declared, 3) if declared > 0 else None

    if war_declared_ticks and war_ended_ticks:
        pairs = list(zip(sorted(war_declared_ticks), sorted(war_ended_ticks)))
        durations = [max(0, e - d) for d, e in pairs]
        metrics['war_duration_mean'] = round(mean(durations), 1) if durations else None
    else:
        metrics['war_duration_mean'] = None

    metrics['first_war_tick']   = first_ticks.get('war_declared')
    metrics['first_tech_tick']  = first_ticks.get('tech_discovered')

    # Writing is a Civic branch technology.
    metrics['first_writing_tick'] = first_ticks.get('tech_civic')

    disruption_keys = ['plague', 'great_migration', 'civil_war', 'promised_land', 'prophet']
    metrics['disruptions_total'] = sum(metrics[k] for k in disruption_keys)

    metrics['deaths_total'] = metrics['death_starvation'] + metrics['death_battle']

    total_deaths = metrics['deaths_total']
    metrics['battle_death_fraction'] = (
        round(metrics['death_battle'] / total_deaths, 3) if total_deaths > 0 else None
    )

    if any(era_counts.values()):
        metrics['dominant_era'] = max(era_counts, key=era_counts.get)
    else:
        metrics['dominant_era'] = 'Unknown'

    for label in ERA_LABELS:
        safe_key = 'era_' + label.lower().replace(' ', '_').replace("'", '')
        metrics[safe_key] = era_counts[label]

    return metrics


def build_csv(all_metrics: list[dict], output_path: str) -> None:
    if not all_metrics:
        return

    base_keys    = ['run_id', 'filepath', 'lines_parsed', 'parse_errors']
    derived_keys = [
        'war_resolution_rate', 'war_duration_mean', 'first_war_tick',
        'first_tech_tick', 'first_writing_tick',
        'disruptions_total', 'deaths_total', 'battle_death_fraction',
        'dominant_era',
    ]
    era_keys     = sorted(k for k in all_metrics[0] if k.startswith('era_'))
    skip         = set(base_keys + derived_keys + era_keys + ['filepath', 'lines_parsed', 'parse_errors'])
    event_keys   = sorted(k for k in all_metrics[0] if k not in skip)

    fieldnames = base_keys + event_keys + derived_keys + era_keys

    with open(output_path, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(all_metrics)


def build_report(all_metrics: list[dict], output_path: str, log_count: int) -> None:

    def _stat(values, fmt='.1f'):
        values = [v for v in values if v is not None]
        if not values:
            return 'N/A'
        pattern = f'{{:{fmt}}}'
        return (f"mean {pattern.format(mean(values))}, "
                f"median {pattern.format(median(values))}, "
                f"range {pattern.format(min(values))}–{pattern.format(max(values))}")

    def _col(key):
        return [m.get(key) for m in all_metrics]

    lines = []
    lines.append('=' * 70)
    lines.append('  THALREN VALE — LOG ANALYSIS REPORT')
    lines.append(f'  {log_count} run(s) analysed')
    lines.append('=' * 70)
    lines.append('')

    lines.append('── WARFARE ──────────────────────────────────────────────────────────')
    lines.append(f"  Declarations per run:      {_stat(_col('war_declared'), '.1f')}")
    lines.append(f"  Resolutions per run:       {_stat(_col('war_ends'), '.1f')}")
    lines.append(f"  Mean war duration:         {_stat(_col('war_duration_mean'), '.1f')} ticks")
    lines.append(f"  First war tick:            {_stat(_col('first_war_tick'), '.0f')}")
    lines.append('')

    lines.append('── DISRUPTION EVENTS ────────────────────────────────────────────────')
    for key in ['plague', 'great_migration', 'civil_war', 'promised_land', 'prophet']:
        lines.append(f"  {key.replace('_', ' ').title():<22} {_stat(_col(key), '.1f')} per run")
    lines.append(f"  Total disruptions:      {_stat(_col('disruptions_total'), '.1f')} per run")
    lines.append('')

    lines.append('── TECHNOLOGY ───────────────────────────────────────────────────────')
    lines.append(f"  Discoveries per run:    {_stat(_col('tech_discovered'), '.1f')}")
    lines.append(f"  Industrial branch:      {_stat(_col('tech_industrial'), '.1f')} per run")
    lines.append(f"  Martial branch:         {_stat(_col('tech_martial'), '.1f')} per run")
    lines.append(f"  Civic branch:           {_stat(_col('tech_civic'), '.1f')} per run")
    lines.append(f"  Unclassified:           (total minus above three)")
    lines.append(f"  First discovery tick:   {_stat(_col('first_tech_tick'), '.0f')}")
    lines.append(f"  First civic tech tick:  {_stat(_col('first_writing_tick'), '.0f')}  (proxy for Writing unlock)")
    lines.append('')

    lines.append('── FACTION LIFECYCLE ────────────────────────────────────────────────')
    lines.append(f"  Formations per run:     {_stat(_col('faction_formed'), '.1f')}")
    lines.append(f"  Schisms per run:        {_stat(_col('schism'), '.1f')}")
    lines.append(f"  Mergers per run:        {_stat(_col('faction_merge'), '.1f')}")
    lines.append('')

    lines.append('── DIPLOMACY ────────────────────────────────────────────────────────')
    lines.append(f"  Treaties formed:        {_stat(_col('treaty_formed'), '.1f')} per run")
    lines.append(f"  Treaties broken:        {_stat(_col('treaty_broken'), '.1f')} per run")
    lines.append('')

    lines.append('── POPULATION ───────────────────────────────────────────────────────')
    lines.append(f"  Births per run:         {_stat(_col('birth'), '.1f')}")
    lines.append(f"  Starvation deaths:      {_stat(_col('death_starvation'), '.1f')} per run")
    lines.append(f"  Battle deaths:          {_stat(_col('death_battle'), '.1f')} per run")
    lines.append(f"  Battle death fraction:  {_stat(_col('battle_death_fraction'), '.3f')}")
    lines.append('')

    lines.append('── RELIGION ─────────────────────────────────────────────────────────')
    lines.append(f"  Religions founded:      {_stat(_col('religion_founded'), '.1f')} per run")
    lines.append(f"  Holy wars declared:     {_stat(_col('holy_war'), '.1f')} per run")
    lines.append(f"  Temples built:          {_stat(_col('temple_built'), '.1f')} per run")
    lines.append('')

    lines.append('── ERA DISTRIBUTION ─────────────────────────────────────────────────')
    era_totals = {}
    for label in ERA_LABELS:
        safe_key = 'era_' + label.lower().replace(' ', '_').replace("'", '')
        total = sum(m.get(safe_key, 0) for m in all_metrics)
        era_totals[label] = total
    grand_total = sum(era_totals.values()) or 1
    for label, count in sorted(era_totals.items(), key=lambda x: -x[1]):
        pct = count / grand_total * 100
        lines.append(f"  {label:<35} {count:>5} occurrences ({pct:.1f}%)")
    lines.append('')

    lines.append('=' * 70)

    with open(output_path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')


def main():
    parser = argparse.ArgumentParser(
        description='Analyse Thalren Vale run_*.txt log files and produce event summaries.'
    )
    parser.add_argument('--log-dir',    default='.',                   help='Directory containing log files')
    parser.add_argument('--pattern',    default='run_*.txt',           help='Glob pattern for log files')
    parser.add_argument('--csv-out',    default='run_event_summary.csv', help='Output CSV path')
    parser.add_argument('--report-out', default='analysis_report.txt', help='Output report path')
    args = parser.parse_args()

    log_files = sorted(glob.glob(os.path.join(args.log_dir, args.pattern)))

    if not log_files:
        print(f"No files found matching '{args.pattern}' in '{args.log_dir}'.")
        sys.exit(1)

    print(f"Found {len(log_files)} log file(s). Parsing...")

    all_metrics = []
    for filepath in log_files:
        name = Path(filepath).name
        metrics = parse_run(filepath)
        all_metrics.append(metrics)
        ind = metrics['tech_industrial']
        mar = metrics['tech_martial']
        civ = metrics['tech_civic']
        unk = metrics['tech_discovered'] - ind - mar - civ
        print(f"  {name}: {metrics['lines_parsed']:,} lines — "
              f"{metrics['war_declared']} wars, "
              f"{metrics['tech_discovered']} techs "
              f"(I:{ind} M:{mar} C:{civ} ?:{unk}), "
              f"{metrics['disruptions_total']} disruptions, "
              f"{metrics['treaty_formed']} treaties")

    build_csv(all_metrics, args.csv_out)
    build_report(all_metrics, args.report_out, len(log_files))

    print(f"\nDone.")
    print(f"  Event summary → {args.csv_out}")
    print(f"  Report        → {args.report_out}")
    print()
    print("NOTE: If any branch column (I/M/C) shows zeros after re-running against")
    print("real logs, inspect the actual log format with:")
    print("  grep -i 'branch\\|tech discovered' logs/run_001.txt | head -20")
    print("and adjust BRANCH_PATTERNS or the inline fallback regexes accordingly.")


if __name__ == '__main__':
    main()
