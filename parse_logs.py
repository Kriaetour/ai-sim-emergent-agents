"""
parse_logs.py — Thalren Vale Log Parser
========================================
Scrapes Tick, Pop, Factions, and Tension from raw run_*.txt simulation logs
and consolidates the data into a single results.csv file.

Usage:
    python parse_logs.py --log-dir ./logs --output results.csv

Expected log line formats (any of the following are handled):
    [Tick 1234] Pop: 456 | Factions: 7 | Tension: 183
    Tick=1234, Pop=456, Factions=7, Tension=183
    [T:1234] population=456 factions=7 tension=183
    Tick 1234 | Pop 456 | Factions 7 | Tension 183

If your logs use a different format, add a pattern to PATTERNS below.
The parser tries all patterns in order and uses the first match per line.
"""

import argparse
import csv
import glob
import os
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Pattern library — add new patterns here as needed
# Each pattern must define four named groups: tick, pop, factions, tension
# ---------------------------------------------------------------------------
PATTERNS = [
    # [Tick 1234] Pop: 456 | Factions: 7 | Tension: 183
    re.compile(
        r"\[Tick\s+(?P<tick>\d+)\]"
        r".*?Pop(?:ulation)?[:\s=]+(?P<pop>\d+)"
        r".*?Factions?[:\s=]+(?P<factions>\d+)"
        r".*?Tension[:\s=]+(?P<tension>[\d.]+)",
        re.IGNORECASE,
    ),
    # Tick=1234, Pop=456, Factions=7, Tension=183  (comma/space delimited k=v)
    re.compile(
        r"Tick[=\s]+(?P<tick>\d+)"
        r".*?Pop(?:ulation)?[=\s]+(?P<pop>\d+)"
        r".*?Factions?[=\s]+(?P<factions>\d+)"
        r".*?Tension[=\s]+(?P<tension>[\d.]+)",
        re.IGNORECASE,
    ),
    # [T:1234] population=456 factions=7 tension=183
    re.compile(
        r"\[T:(?P<tick>\d+)\]"
        r".*?pop(?:ulation)?[=:\s]+(?P<pop>\d+)"
        r".*?factions?[=:\s]+(?P<factions>\d+)"
        r".*?tension[=:\s]+(?P<tension>[\d.]+)",
        re.IGNORECASE,
    ),
    # Fallback: any line containing all four keywords with adjacent numbers
    re.compile(
        r"(?:tick|t)[^\d]*(?P<tick>\d+)"
        r".*?(?:pop|population)[^\d]*(?P<pop>\d+)"
        r".*?(?:faction)[^\d]*(?P<factions>\d+)"
        r".*?(?:tension)[^\d]*(?P<tension>[\d.]+)",
        re.IGNORECASE,
    ),
]

OUTPUT_FIELDS = ["run_id", "tick", "pop", "factions", "tension"]

# ---------------------------------------------------------------------------
# TUI (box-drawing) format — produced by newer simulator versions
# Header:  │  Tick 001/10000  ☀ Summer  Alive:30/30  Deaths: 0  Factions:0 …│
# Tension: ⚔ Rivalry: FactionA vs FactionB — tension: 3
# Tension is aggregated by summing all rivalry lines within a tick block.
# ---------------------------------------------------------------------------
_TUI_TICK_HEADER = re.compile(
    r"Tick\s+(?P<tick>\d+)/\d+.*?Alive:(?P<pop>\d+)/\d+.*?Factions:(?P<factions>\d+)",
    re.IGNORECASE,
)
_TUI_RIVALRY = re.compile(r"—\s*tension:\s*(?P<tension>[\d.]+)", re.IGNORECASE)


def extract_run_id(filepath: str) -> str:
    """Derive a run identifier from the filename.

    Handles both formats:
      - Date-stamped:  run_20260227_054559.txt  → '20260227_054559'
      - Numeric:       run_042.txt              → '042'
      - Fallback:      anything_else.txt        → stem as-is
    """
    stem = Path(filepath).stem
    # Date-stamped: YYYYMMDD_HHMMSS
    m = re.match(r"run_(\d{8}_\d{6})$", stem)
    if m:
        return m.group(1)
    m2 = re.match(r"run_(\d+)$", stem)
    if m2:
        return m2.group(1)
    if stem.startswith("run_"):
        return stem[4:]
    return stem


def parse_line(line: str) -> dict | None:
    """Attempt to match a log line against all known patterns. Returns a dict or None."""
    for pattern in PATTERNS:
        m = pattern.search(line)
        if m:
            return {
                "tick":     int(m.group("tick")),
                "pop":      int(m.group("pop")),
                "factions": int(m.group("factions")),
                "tension":  float(m.group("tension")),
            }
    return None


def _is_tui_format(filepath) -> bool:
    """Peek at the first 100 lines to detect the box-drawing TUI log format."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i >= 100:
                break
            if _TUI_TICK_HEADER.search(line):
                return True
    return False


def _parse_file_tui(filepath) -> list[dict]:
    """Stateful parser for the box-drawing TUI log format.

    Each tick block starts with a header line carrying tick / pop / factions.
    Tension is the SUM of all per-rivalry tension values within that tick block.
    """
    rows = []
    current: dict | None = None
    current_tension = 0.0

    with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            header_m = _TUI_TICK_HEADER.search(line)
            if header_m:
                if current is not None:
                    rows.append({**current, "tension": current_tension})
                current = {
                    "tick":     int(header_m.group("tick")),
                    "pop":      int(header_m.group("pop")),
                    "factions": int(header_m.group("factions")),
                }
                current_tension = 0.0
                continue
            if current is not None:
                rivalry_m = _TUI_RIVALRY.search(line)
                if rivalry_m:
                    current_tension += float(rivalry_m.group("tension"))

    if current is not None:
        rows.append({**current, "tension": current_tension})

    return rows


def parse_file(filepath) -> list[dict]:
    """Parse a single log file, returning a list of {tick, pop, factions, tension} dicts.

    Automatically detects TUI (box-drawing) vs flat key=value format.
    run_id is NOT attached here — callers (e.g. main) are responsible for that.
    """
    if _is_tui_format(filepath):
        return _parse_file_tui(filepath)

    # --- flat key=value format (original four variants) ---
    rows = []
    unmatched_count = 0
    with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parsed = parse_line(line)
            if parsed:
                rows.append(parsed)
            else:
                lcase = line.lower()
                if any(k in lcase for k in ["tick", "pop", "tension"]):
                    unmatched_count += 1
    if unmatched_count:
        print(f"WARNING: {unmatched_count} data-like lines in '{Path(filepath).name}' did not match any pattern.")
    return rows


def main():
    parser = argparse.ArgumentParser(
        description="Parse Thalren Vale run_*.txt logs into results.csv"
    )
    parser.add_argument(
        "--log-dir", default=".", help="Directory containing run_*.txt files (default: current dir)"
    )
    parser.add_argument(
        "--output", default="results.csv", help="Output CSV path (default: results.csv)"
    )
    parser.add_argument(
        "--pattern", default="run_*.txt",
        help="Glob pattern for log files (default: run_*.txt). "
             "Matches both run_20260227_054559.txt and run_042.txt style names."
    )
    parser.add_argument(
        "--sample", action="store_true",
        help="Print the first 5 parsed rows from each file for verification"
    )
    args = parser.parse_args()

    log_glob = os.path.join(args.log_dir, args.pattern)
    log_files = sorted(glob.glob(log_glob))

    if not log_files:
        print(f"ERROR: No files found matching '{log_glob}'")
        sys.exit(1)

    print(f"Found {len(log_files)} log file(s) in '{args.log_dir}'")

    all_rows = []
    for filepath in log_files:
        run_id = extract_run_id(filepath)
        rows = parse_file(filepath)
        print(f"  {Path(filepath).name}: {len(rows)} ticks parsed")
        if args.sample and rows:
            for r in rows[:5]:
                print(f"    {r}")
        for row in rows:
            all_rows.append({"run_id": run_id, **row})

    if not all_rows:
        print(
            "\nERROR: No data rows were extracted. Check that your log format matches "
            "one of the patterns in PATTERNS, or add a new pattern."
        )
        sys.exit(1)

    # Sort by run_id then tick for readability
    all_rows.sort(key=lambda r: (r["run_id"], r["tick"]))

    output_path = Path(args.output)
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nDone. {len(all_rows)} total rows written to '{output_path}'")
    print("Columns: run_id, tick, pop, factions, tension")
    print("\nNext step: load results.csv into pandas or R to generate your figures.")
    print("  Example:")
    print("    import pandas as pd")
    print("    df = pd.read_csv('results.csv')")
    print("    df.groupby('run_id')['pop'].plot()  # sawtooth population curves")


if __name__ == "__main__":
    main()
