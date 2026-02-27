#!/usr/bin/env python3
# (c) 2026 (KriaetvAspie / AspieTheBard)
# Licensed under the Polyform Noncommercial License 1.0.0
"""
run_experiments.py — Batch runner for Thalren Vale simulation experiments.

Usage examples
──────────────
    # Single seed, default (baseline) condition
    python run_experiments.py --seeds 1-5

    # Named condition with overrides
    python run_experiments.py --seeds 1-20 --condition no_antistag --extra-args "--disable-antistag"

    # From an experiment plan file
    python run_experiments.py --plan experiments.json

    # Verify outputs exist after a batch
    python run_experiments.py --verify --plan experiments.json
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


# ── Helpers ────────────────────────────────────────────────────────────────

def parse_seed_range(spec: str) -> list:
    """Parse a seed specification like '1-20' or '1,3,5' or '42' into a list.

    Supports:
        '1-20'      → [1, 2, ..., 20]
        '1,3,5,10'  → [1, 3, 5, 10]
        '42'         → [42]
    """
    seeds = []
    for part in spec.split(','):
        part = part.strip()
        if '-' in part:
            lo, hi = part.split('-', 1)
            seeds.extend(range(int(lo), int(hi) + 1))
        else:
            seeds.append(int(part))
    return seeds


def run_single(seed: int, condition: str, ticks: int,
               extra_args: list, output_dir: str = 'data') -> dict:
    """Run one simulation as a subprocess.  Returns a result dict."""
    cmd = [
        sys.executable, '-m', 'thalren_vale',
        '--seed', str(seed),
        '--condition', condition,
        '--ticks', str(ticks),
    ] + extra_args

    print(f'  [{condition}] seed={seed}  ...', end='', flush=True)
    t0 = time.time()

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=max(600, ticks // 2),  # generous timeout
    )

    elapsed = round(time.time() - t0, 1)
    ok = result.returncode == 0
    status = 'OK' if ok else f'FAIL(rc={result.returncode})'
    print(f'  {status}  ({elapsed}s)')

    if not ok:
        # Print truncated stderr for debugging
        stderr = result.stderr.strip()
        if stderr:
            lines = stderr.splitlines()
            for line in lines[-10:]:
                print(f'    | {line}')

    return {
        'seed': seed,
        'condition': condition,
        'ok': ok,
        'elapsed': elapsed,
        'returncode': result.returncode,
    }


def run_batch(seeds: list, condition: str, ticks: int,
              extra_args: list) -> list:
    """Run a batch of seeds for one condition, sequentially."""
    results = []
    for seed in seeds:
        try:
            r = run_single(seed, condition, ticks, extra_args)
            results.append(r)
        except subprocess.TimeoutExpired:
            print(f'  [{condition}] seed={seed}  TIMEOUT')
            results.append({
                'seed': seed, 'condition': condition,
                'ok': False, 'elapsed': 0, 'returncode': -1,
            })
        except Exception as exc:
            print(f'  [{condition}] seed={seed}  ERROR: {exc}')
            results.append({
                'seed': seed, 'condition': condition,
                'ok': False, 'elapsed': 0, 'returncode': -1,
            })
    return results


def run_from_plan(plan_path: str) -> list:
    """Load an experiment plan JSON and execute every condition × seed."""
    with open(plan_path, 'r', encoding='utf-8') as f:
        plan = json.load(f)

    all_results = []
    conditions = plan.get('conditions', [])
    default_ticks = plan.get('default_ticks', 10000)

    print(f'\n{"=" * 60}')
    print(f'  Experiment plan: {plan_path}')
    print(f'  Conditions: {len(conditions)}')
    print(f'  Default ticks: {default_ticks}')
    print(f'{"=" * 60}\n')

    for cond in conditions:
        name = cond['name']
        seeds = parse_seed_range(cond.get('seeds', '1-5'))
        ticks = cond.get('ticks', default_ticks)
        extra = cond.get('extra_args', [])
        if isinstance(extra, str):
            extra = extra.split()

        print(f'\n── Condition: {name}  ({len(seeds)} seeds, {ticks} ticks) ──')
        results = run_batch(seeds, name, ticks, extra)
        all_results.extend(results)

        # Summary for this condition
        ok_n = sum(1 for r in results if r['ok'])
        fail_n = len(results) - ok_n
        total_t = sum(r['elapsed'] for r in results)
        print(f'   Done: {ok_n} OK, {fail_n} FAIL  ({total_t:.0f}s total)')

    # Overall summary
    print(f'\n{"=" * 60}')
    ok_total = sum(1 for r in all_results if r['ok'])
    fail_total = len(all_results) - ok_total
    print(f'  Overall: {ok_total}/{len(all_results)} OK, {fail_total} FAIL')
    print(f'{"=" * 60}\n')

    return all_results


def verify_outputs(plan_path: str, output_dir: str = 'data') -> bool:
    """Check that expected CSV files exist for every condition × seed."""
    with open(plan_path, 'r', encoding='utf-8') as f:
        plan = json.load(f)

    missing = []
    for cond in plan.get('conditions', []):
        seeds = parse_seed_range(cond.get('seeds', '1-5'))
        for seed in seeds:
            csv_path = os.path.join(output_dir, f'metrics_seed_{seed}.csv')
            if not os.path.isfile(csv_path):
                missing.append((cond['name'], seed, csv_path))

    summary_path = os.path.join(output_dir, 'run_summaries.csv')
    if not os.path.isfile(summary_path):
        missing.append(('*', '*', summary_path))

    if missing:
        print(f'\n  ✗ {len(missing)} missing output(s):')
        for cond, seed, path in missing[:20]:
            print(f'    [{cond}] seed={seed}: {path}')
        if len(missing) > 20:
            print(f'    ... and {len(missing) - 20} more')
        return False
    else:
        print('  ✓ All expected outputs found.')
        return True


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Batch runner for Thalren Vale simulation experiments')

    parser.add_argument('--seeds', type=str, default=None,
                        help='Seed range, e.g. "1-100" or "1,5,10"')
    parser.add_argument('--condition', type=str, default='baseline',
                        help='Condition label')
    parser.add_argument('--ticks', type=int, default=10000,
                        help='Ticks per run (default: 10000)')
    parser.add_argument('--extra-args', type=str, default='',
                        help='Extra CLI arguments passed to the sim (quoted string)')
    parser.add_argument('--plan', type=str, default=None,
                        help='Path to experiment plan JSON')
    parser.add_argument('--verify', action='store_true',
                        help='Verify output files exist (use with --plan)')

    args = parser.parse_args()

    if args.verify and args.plan:
        ok = verify_outputs(args.plan)
        sys.exit(0 if ok else 1)

    if args.plan:
        run_from_plan(args.plan)
    elif args.seeds:
        seeds = parse_seed_range(args.seeds)
        extra = args.extra_args.split() if args.extra_args else []
        print(f'\n-- Batch: {args.condition}  ({len(seeds)} seeds, {args.ticks} ticks) --')
        results = run_batch(seeds, args.condition, args.ticks, extra)
        ok_n = sum(1 for r in results if r['ok'])
        print(f'\n  Done: {ok_n}/{len(results)} OK')
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
