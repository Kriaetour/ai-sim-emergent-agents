"""Analyze Reverse Assimilation tracking data across all baseline seeds."""
import pandas as pd
import numpy as np
from pathlib import Path
from scipy import stats

data_dir = Path("data")

# Load all followup files
followup_files = sorted(data_dir.glob("ra_followups_baseline_seed_*.csv"))
annex_files = sorted(data_dir.glob("ra_annexations_baseline_seed_*.csv"))

print(f"Found {len(followup_files)} followup files, {len(annex_files)} annexation files\n")

# --- ANNEXATION EVENTS ---
all_annex = []
for f in annex_files:
    df = pd.read_csv(f)
    all_annex.append(df)
annex_df = pd.concat(all_annex, ignore_index=True)

print("=" * 70)
print("ANNEXATION/MERGE EVENT SUMMARY")
print("=" * 70)
print(f"Total events across {annex_df['seed'].nunique()} seeds: {len(annex_df)}")
print(f"\nBy merge type:")
print(annex_df['merge_type'].value_counts().to_string())
print(f"\nBy seed:")
for seed in sorted(annex_df['seed'].unique()):
    sub = annex_df[annex_df['seed'] == seed]
    n_annex = (sub['merge_type'] == 'annexation').sum()
    n_vol = (sub['merge_type'] == 'voluntary_merge').sum()
    print(f"  Seed {seed}: {n_annex} annexations, {n_vol} voluntary merges (total {len(sub)})")

# --- FOLLOWUP DATA ---
all_follow = []
for f in followup_files:
    df = pd.read_csv(f)
    all_follow.append(df)
follow_df = pd.concat(all_follow, ignore_index=True)

# Filter to rows where the faction still exists (has members and cosine data)
valid = follow_df[(follow_df['host_members_now'] > 0) & (follow_df['cosine_vs_absorbed_pre'].notna())].copy()

print(f"\n{'=' * 70}")
print("FOLLOWUP TRACKING SUMMARY")
print("=" * 70)
print(f"Total followup rows: {len(follow_df)}")
print(f"Valid (faction survived with data): {len(valid)}")
print(f"Dissolved before followup: {(follow_df['host_members_now'] == 0).sum()}")

print(f"\nValid followups by merge type:")
print(valid['merge_type'].value_counts().to_string())

print(f"\nValid followups by offset:")
print(valid['offset'].value_counts().sort_index().to_string())

# --- COSINE SIMILARITY ANALYSIS ---
print(f"\n{'=' * 70}")
print("COSINE SIMILARITY ANALYSIS")
print("=" * 70)

for mt in ['annexation', 'voluntary_merge']:
    sub = valid[valid['merge_type'] == mt]
    if len(sub) == 0:
        print(f"\n--- {mt}: NO DATA ---")
        continue
    
    print(f"\n--- {mt} (n={len(sub)}) ---")
    
    for offset in sorted(sub['offset'].unique()):
        osub = sub[sub['offset'] == offset]
        if len(osub) == 0:
            continue
        
        cos_abs = osub['cosine_vs_absorbed_pre']
        cos_host = osub['cosine_vs_host_pre']
        
        # Belief drift ratio: how much does host move toward absorbed vs stay near original?
        # cosine_vs_absorbed_pre > cosine_vs_host_pre would indicate reverse assimilation
        drift = cos_abs - cos_host  # positive = moved toward absorbed
        
        print(f"\n  Offset t+{offset} (n={len(osub)}):")
        print(f"    cosine_vs_absorbed_pre: mean={cos_abs.mean():.4f}, sd={cos_abs.std():.4f}, median={cos_abs.median():.4f}")
        print(f"    cosine_vs_host_pre:     mean={cos_host.mean():.4f}, sd={cos_host.std():.4f}, median={cos_host.median():.4f}")
        print(f"    drift (absorbed-host):   mean={drift.mean():.4f}, sd={drift.std():.4f}")
        
        # One-sample t-test: is drift significantly different from 0?
        if len(osub) >= 3:
            t_stat, p_val = stats.ttest_1samp(drift.dropna(), 0)
            print(f"    t-test drift≠0: t={t_stat:.3f}, p={p_val:.4f} {'*' if p_val < 0.05 else ''}")
        
        # Sign test: how many cases show positive drift (movement toward absorbed)?
        n_positive = (drift > 0).sum()
        n_negative = (drift < 0).sum()
        n_zero = (drift == 0).sum()
        print(f"    Direction: {n_positive} toward absorbed, {n_negative} toward host, {n_zero} neutral")

# --- COMPARING ANNEXATION vs VOLUNTARY MERGE ---
print(f"\n{'=' * 70}")
print("COMPARISON: ANNEXATION vs VOLUNTARY MERGE")
print("=" * 70)

for offset in sorted(valid['offset'].unique()):
    annex_sub = valid[(valid['merge_type'] == 'annexation') & (valid['offset'] == offset)]
    vol_sub = valid[(valid['merge_type'] == 'voluntary_merge') & (valid['offset'] == offset)]
    
    if len(annex_sub) < 2 or len(vol_sub) < 2:
        print(f"\nOffset t+{offset}: insufficient data (annex n={len(annex_sub)}, vol n={len(vol_sub)})")
        continue
    
    annex_drift = annex_sub['cosine_vs_absorbed_pre'] - annex_sub['cosine_vs_host_pre']
    vol_drift = vol_sub['cosine_vs_absorbed_pre'] - vol_sub['cosine_vs_host_pre']
    
    t_stat, p_val = stats.ttest_ind(annex_drift.dropna(), vol_drift.dropna(), equal_var=False)
    print(f"\nOffset t+{offset}: annex drift mean={annex_drift.mean():.4f} (n={len(annex_sub)}), "
          f"vol drift mean={vol_drift.mean():.4f} (n={len(vol_sub)})")
    print(f"  Welch's t-test: t={t_stat:.3f}, p={p_val:.4f} {'*' if p_val < 0.05 else ''}")

# --- PER-SEED SUMMARY ---
print(f"\n{'=' * 70}")
print("PER-SEED ANNEXATION DRIFT SUMMARY")
print("=" * 70)
for seed in sorted(valid['seed'].unique()):
    seed_data = valid[(valid['seed'] == seed) & (valid['merge_type'] == 'annexation')]
    if len(seed_data) == 0:
        print(f"  Seed {seed}: 0 valid annexation followups")
        continue
    drift = seed_data['cosine_vs_absorbed_pre'] - seed_data['cosine_vs_host_pre']
    print(f"  Seed {seed}: {len(seed_data)} valid annexation followups, "
          f"mean drift={drift.mean():.4f}, range=[{drift.min():.4f}, {drift.max():.4f}]")

# --- OVERALL SUMMARY ---
print(f"\n{'=' * 70}")
print("OVERALL SUMMARY FOR PAPER")
print("=" * 70)
total_events = len(annex_df)
n_annex = (annex_df['merge_type'] == 'annexation').sum()
n_vol = (annex_df['merge_type'] == 'voluntary_merge').sum()
n_seeds = annex_df['seed'].nunique()
valid_annex = valid[valid['merge_type'] == 'annexation']
valid_vol = valid[valid['merge_type'] == 'voluntary_merge']

print(f"Seeds analyzed: {n_seeds}")
print(f"Total merge/annexation events captured: {total_events}")
print(f"  Annexations (military conquest): {n_annex}")
print(f"  Voluntary merges (trust-based):  {n_vol}")
print(f"Valid followup observations (faction survived): {len(valid)}")
print(f"  From annexations: {len(valid_annex)}")
print(f"  From voluntary merges: {len(valid_vol)}")

if len(valid_annex) > 0:
    annex_drift_all = valid_annex['cosine_vs_absorbed_pre'] - valid_annex['cosine_vs_host_pre']
    print(f"\nAnnexation belief drift (all offsets):")
    print(f"  Mean: {annex_drift_all.mean():.4f} (positive = toward absorbed faction's beliefs)")
    print(f"  SD:   {annex_drift_all.std():.4f}")
    n_pos = (annex_drift_all > 0).sum()
    print(f"  Direction: {n_pos}/{len(annex_drift_all)} cases show movement toward absorbed beliefs")

if len(valid_vol) > 0:
    vol_drift_all = valid_vol['cosine_vs_absorbed_pre'] - valid_vol['cosine_vs_host_pre']
    print(f"\nVoluntary merge belief drift (all offsets):")
    print(f"  Mean: {vol_drift_all.mean():.4f}")
    print(f"  SD:   {vol_drift_all.std():.4f}")
    n_pos = (vol_drift_all > 0).sum()
    print(f"  Direction: {n_pos}/{len(vol_drift_all)} cases show movement toward absorbed beliefs")
