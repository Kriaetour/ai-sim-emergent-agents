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


# ── Event patterns ────────────────────────────────────────────────────────────
# Each entry: (key, compiled_regex)
# The tick number is captured in group 1 of every pattern where timing matters.

# Shared pattern to extract tick number from any matched line.
# Used alongside keyword-only PATTERNS: first check if the keyword is on the
# line, then pull the tick with TICK_RE.  Separating the two concerns avoids
# all group-numbering and repeated-named-group issues across alternation.
TICK_RE = re.compile(r'Tick\s+(\d+)')


def _p(*keywords, flags=re.I):
    """Return a compiled pattern that matches any of the given keywords.

    Does NOT capture the tick — use TICK_RE.search(line).group(1) for that.
    Each keyword is anchored with a preceding Tick token so lines without a
    tick prefix are never counted as events.
    """
    alt = '|'.join(rf'(?:Tick[^\n]*?{kw})' for kw in keywords)
    return re.compile(f'(?:{alt})', flags)


PATTERNS = {
    # Wars
    'war_declared':        _p('WAR DECLARED'),
    'war_ends':            _p('WAR ENDS'),
    'democratic_override': _p('INCIDENT', 'council[^\n]*?override', 'tension[^\n]*?override'),

    # Disruption events (anti-stagnation system)
    'plague':              _p('PLAGUE SWEEPS'),
    'great_migration':     _p('GREAT MIGRATION'),
    'civil_war':           _p('CIVIL WAR'),
    'promised_land':       _p('PROMISED LAND'),
    'prophet':             _p('PROPHET arrives', 'PROPHET cries'),

    # Faction lifecycle
    'faction_formed':      _p('FACTION FORMED', r'FACTION\s*[—–\-]+[^\n]*?formed'),
    'schism':              _p('SCHISM'),
    'faction_merge':       _p('FACTION MERGE', 'DIPLOMATIC MERGE', 'MERGER'),

    # Technology
    'tech_discovered':     _p('TECH DISCOVERED'),
    'tech_agriculture':    _p('TECH DISCOVERED[^\n]*?(?:Farming|Agriculture|Tools|Sawmill|Smelting)'),
    'tech_military':       _p('TECH DISCOVERED[^\n]*?(?:Weapons|Fortifications|Tactics|Cavalry|Siege)'),
    'tech_knowledge':      _p('TECH DISCOVERED[^\n]*?(?:Writing|Mathematics|Philosophy|Code of Laws|Navigation)'),

    # Diplomacy
    'treaty_formed':       _p('TREATY[^\n]*?(?:signed|formed|agreed|established)'),
    'treaty_broken':       _p('broke treaty', 'treaty[^\n]*?broken'),

    # Population events
    'birth':               _p('BIRTH'),
    'death_starvation':    _p('starved', 'wasted away'),
    'death_battle':        _p('fell in battle'),

    # Era shifts
    'era_shift':           _p('NEW ERA DAWNS', 'ERA SUMMARY'),

    # World events (every 200 ticks)
    'world_event':         _p('WORLD EVENT'),

    # Religion (Layer 9)
    'religion_founded':    _p('RELIGION FOUNDED'),
    'holy_war':            _p('HOLY WAR'),
    'temple_built':        _p('TEMPLE BUILT'),
}

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
        'run_id':          run_id,
        'filepath':        str(filepath),
        'lines_parsed':    0,
        'parse_errors':    0,
    }

    # Initialise all event counters to 0
    for key in PATTERNS:
        metrics[key] = 0

    # Tick lists for derived timing metrics
    first_ticks = {}          # key → tick of first occurrence
    war_declared_ticks = []
    war_ended_ticks = []
    tech_ticks = []

    # Era label counts
    era_counts = {label: 0 for label in ERA_LABELS}

    with open(filepath, 'r', encoding='utf-8', errors='replace') as fh:
            for raw_line in fh:
                metrics['lines_parsed'] += 1
                line = raw_line.strip()
                if not line:
                    continue

                # Match against all patterns — exception handling is per-line so
                # one malformed line cannot abort processing the rest of the file.
                for key, pattern in PATTERNS.items():
                    try:
                        m = pattern.search(line)
                        if not m:
                            continue
                        metrics[key] += 1
                        # Extract tick via the shared TICK_RE — always group(1)
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
                    except Exception:
                        metrics['parse_errors'] += 1

                # Era label counting
                for label in ERA_LABELS:
                    if label in line:
                        era_counts[label] += 1

    # ── Derived metrics ───────────────────────────────────────────────────────

    # War resolution rate
    declared = metrics['war_declared']
    ended    = metrics['war_ends']
    metrics['war_resolution_rate'] = round(ended / declared, 3) if declared > 0 else None

    # Mean war duration (ticks between paired declarations and resolutions)
    if war_declared_ticks and war_ended_ticks:
        pairs = list(zip(sorted(war_declared_ticks), sorted(war_ended_ticks)))
        durations = [max(0, e - d) for d, e in pairs]
        metrics['war_duration_mean'] = round(mean(durations), 1) if durations else None
    else:
        metrics['war_duration_mean'] = None

    # First tick of first war
    metrics['first_war_tick'] = first_ticks.get('war_declared')

    # First tech discovery tick
    metrics['first_tech_tick'] = first_ticks.get('tech_discovered')

    # First Writing/Knowledge tech tick (cultural transmission enabler)
    metrics['first_writing_tick'] = first_ticks.get('tech_knowledge')

    # Total disruption events
    disruption_keys = ['plague', 'great_migration', 'civil_war', 'promised_land', 'prophet']
    metrics['disruptions_total'] = sum(metrics[k] for k in disruption_keys)

    # Total deaths
    metrics['deaths_total'] = metrics['death_starvation'] + metrics['death_battle']

    # Battle death fraction
    total_deaths = metrics['deaths_total']
    metrics['battle_death_fraction'] = (
        round(metrics['death_battle'] / total_deaths, 3) if total_deaths > 0 else None
    )

    # Dominant era type
    if any(era_counts.values()):
        metrics['dominant_era'] = max(era_counts, key=era_counts.get)
    else:
        metrics['dominant_era'] = 'Unknown'

    # Append per-era counts
    for label in ERA_LABELS:
        safe_key = 'era_' + label.lower().replace(' ', '_').replace("'", '')
        metrics[safe_key] = era_counts[label]

    return metrics


def build_csv(all_metrics: list[dict], output_path: str) -> None:
    """Write all per-run metrics to a CSV file."""
    if not all_metrics:
        return

    # Collect all keys, preserving insertion order then sorting extras
    base_keys = ['run_id', 'filepath', 'lines_parsed', 'parse_errors']
    event_keys = sorted(k for k in all_metrics[0] if k not in base_keys
                        and not k.startswith('era_') and k != 'dominant_era'
                        and k not in ('filepath', 'lines_parsed', 'parse_errors'))
    derived_keys = ['war_resolution_rate', 'war_duration_mean', 'first_war_tick',
                    'first_tech_tick', 'first_writing_tick',
                    'disruptions_total', 'deaths_total', 'battle_death_fraction',
                    'dominant_era']
    era_keys = sorted(k for k in all_metrics[0] if k.startswith('era_'))

    # Remove derived keys from event_keys to avoid duplication
    event_keys = [k for k in event_keys if k not in derived_keys]

    fieldnames = base_keys + event_keys + derived_keys + era_keys

    with open(output_path, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(all_metrics)


def build_report(all_metrics: list[dict], output_path: str, log_count: int) -> None:
    """Write a human-readable narrative summary to a text file."""

    def _stat(values, fmt='.1f'):
        values = [v for v in values if v is not None]
        if not values:
            return 'N/A'
        pattern = f'{{:{fmt}}}'
        return (f"mean {pattern.format(mean(values))}, "
                f"median {pattern.format(median(values))}, "
                f"range {pattern.format(min(values))}–{pattern.format(max(values))}")

    def _col(key):
        return [m[key] for m in all_metrics]

    lines = []
    lines.append('=' * 70)
    lines.append('  THALREN VALE — LOG ANALYSIS REPORT')
    lines.append(f'  {log_count} run(s) analysed')
    lines.append('=' * 70)
    lines.append('')

    # Wars
    lines.append('── WARFARE ──────────────────────────────────────────────────────────')
    lines.append(f"  Declarations per run:   {_stat(_col('war_declared'), '.1f')}")
    lines.append(f"  Resolutions per run:    {_stat(_col('war_ends'), '.1f')}")
    lines.append(f"  Dem. overrides per run: {_stat(_col('democratic_override'), '.1f')}")
    lines.append(f"  Mean war duration:      {_stat(_col('war_duration_mean'), '.1f')} ticks")
    lines.append(f"  First war tick:         {_stat(_col('first_war_tick'), '.0f')}")
    lines.append('')

    # Disruptions
    lines.append('── DISRUPTION EVENTS ────────────────────────────────────────────────')
    for key in ['plague', 'great_migration', 'civil_war', 'promised_land', 'prophet']:
        lines.append(f"  {key.replace('_', ' ').title():<22} {_stat(_col(key), '.1f')} per run")
    lines.append(f"  Total disruptions:      {_stat(_col('disruptions_total'), '.1f')} per run")
    lines.append('')

    # Technology
    lines.append('── TECHNOLOGY ───────────────────────────────────────────────────────')
    lines.append(f"  Discoveries per run:    {_stat(_col('tech_discovered'), '.1f')}")
    lines.append(f"  Industrial branch:      {_stat(_col('tech_agriculture'), '.1f')} per run")
    lines.append(f"  Military branch:        {_stat(_col('tech_military'), '.1f')} per run")
    lines.append(f"  Knowledge branch:       {_stat(_col('tech_knowledge'), '.1f')} per run")
    lines.append(f"  First discovery tick:   {_stat(_col('first_tech_tick'), '.0f')}")
    lines.append(f"  First writing tick:     {_stat(_col('first_writing_tick'), '.0f')}")
    lines.append('')

    # Factions
    lines.append('── FACTION LIFECYCLE ────────────────────────────────────────────────')
    lines.append(f"  Formations per run:     {_stat(_col('faction_formed'), '.1f')}")
    lines.append(f"  Schisms per run:        {_stat(_col('schism'), '.1f')}")
    lines.append(f"  Mergers per run:        {_stat(_col('faction_merge'), '.1f')}")
    lines.append('')

    # Diplomacy
    lines.append('── DIPLOMACY ────────────────────────────────────────────────────────')
    lines.append(f"  Treaties formed:        {_stat(_col('treaty_formed'), '.1f')} per run")
    lines.append(f"  Treaties broken:        {_stat(_col('treaty_broken'), '.1f')} per run")
    lines.append('')

    # Population events
    lines.append('── POPULATION ───────────────────────────────────────────────────────')
    lines.append(f"  Births per run:         {_stat(_col('birth'), '.1f')}")
    lines.append(f"  Starvation deaths:      {_stat(_col('death_starvation'), '.1f')} per run")
    lines.append(f"  Battle deaths:          {_stat(_col('death_battle'), '.1f')} per run")
    lines.append(f"  Battle death fraction:  {_stat(_col('battle_death_fraction'), '.3f')}")
    lines.append('')

    # Religion
    lines.append('── RELIGION ─────────────────────────────────────────────────────────')
    lines.append(f"  Religions founded:      {_stat(_col('religion_founded'), '.1f')} per run")
    lines.append(f"  Holy wars declared:     {_stat(_col('holy_war'), '.1f')} per run")
    lines.append(f"  Temples built:          {_stat(_col('temple_built'), '.1f')} per run")
    lines.append('')

    # Era distribution
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
    parser.add_argument('--log-dir', default='.', help='Directory containing log files')
    parser.add_argument('--pattern', default='run_*.txt', help='Glob pattern for log files')
    parser.add_argument('--csv-out', default='run_event_summary.csv', help='Output CSV path')
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
        print(f"  {name}: {metrics['lines_parsed']:,} lines — "
              f"{metrics['war_declared']} wars, "
              f"{metrics['tech_discovered']} techs, "
              f"{metrics['disruptions_total']} disruptions")

    build_csv(all_metrics, args.csv_out)
    build_report(all_metrics, args.report_out, len(log_files))

    print(f"\nDone.")
    print(f"  Event summary → {args.csv_out}")
    print(f"  Report        → {args.report_out}")


if __name__ == '__main__':
    main()
