"""Extract all statistics needed to update the paper tables."""
import pandas as pd
import numpy as np

df = pd.read_csv("results.csv")
ev = pd.read_csv("run_event_summary.csv")

print("=" * 60)
print("TABLE 2: Tick-Level Descriptive Statistics")
print("=" * 60)
for col in ["pop", "factions", "tension"]:
    s = df[col]
    q1 = s.quantile(0.25)
    q3 = s.quantile(0.75)
    print(f"{col}:")
    print(f"  mean={s.mean():.2f}  sd={s.std():.2f}  median={s.median():.0f}  iqr={q3 - q1:.0f}  min={s.min():.0f}  max={s.max():.0f}")

print()
print("=" * 60)
print("TABLE 3: Population Events Per Run (N=100)")
print("=" * 60)
for col in ["birth", "death_starvation", "death_battle", "deaths_total"]:
    s = ev[col]
    print(f"{col}: mean={s.mean():.1f}  sd={s.std():.1f}  median={s.median():.0f}  min={s.min():.0f}  max={s.max():.0f}")

# battle death fraction
bdf = ev["death_battle"] / ev["deaths_total"]
print(f"battle_death_fraction: mean={bdf.mean():.3f}  sd={bdf.std():.3f}")

print()
print("=" * 60)
print("TABLE 4: Warfare Statistics Per Run")
print("=" * 60)
for col in ["war_declared", "war_ends", "treaty_formed", "treaty_broken"]:
    s = ev[col]
    print(f"{col}: mean={s.mean():.1f}  sd={s.std():.1f}  total={s.sum():.0f}")

if "war_duration_mean" in ev.columns:
    s = ev["war_duration_mean"].dropna()
    print(f"war_duration_mean: mean={s.mean():.1f}  sd={s.std():.1f}")

s = ev["first_war_tick"]
print(f"first_war_tick: mean={s.mean():.1f}  sd={s.std():.1f}  min={s.min():.0f}  max={s.max():.0f}  median={s.median():.0f}")

print()
print("=" * 60)
print("TABLE 5: Technology Statistics Per Run")
print("=" * 60)
tech_cols = [c for c in ev.columns if c.startswith("tech_")]
for col in tech_cols:
    s = ev[col]
    print(f"{col}: mean={s.mean():.1f}  sd={s.std():.1f}  min={s.min():.0f}  max={s.max():.0f}")

print()
print("=" * 60)
print("TABLE 6: Faction Lifecycle Per Run")
print("=" * 60)
faction_cols = ["faction_formed", "faction_disbanded", "faction_merged"]
for col in faction_cols:
    if col in ev.columns:
        s = ev[col]
        print(f"{col}: mean={s.mean():.1f}  sd={s.std():.1f}  total={s.sum():.0f}")

# Also check for schism, alliance, religion columns
for col in ["schism", "holy_war", "religion_founded", "alliance_formed", "alliance_broken"]:
    if col in ev.columns:
        s = ev[col]
        print(f"{col}: mean={s.mean():.1f}  sd={s.std():.1f}  total={s.sum():.0f}")

print()
print("=" * 60)
print("TABLE 7: Era Distribution")
print("=" * 60)
era_cols = [c for c in ev.columns if c.startswith("era_")]
for col in era_cols:
    s = ev[col]
    print(f"{col}: mean={s.mean():.1f}  sd={s.std():.1f}  min={s.min():.0f}  max={s.max():.0f}")

print()
print("=" * 60)
print("OTHER PROSE STATS")
print("=" * 60)
# total observations
print(f"Total observations (rows in results.csv): {len(df)}")
print(f"Number of runs: {ev.shape[0]}")

# Column listing
print(f"\nAll event summary columns:\n{list(ev.columns)}")

# Extra columns that might be referenced
for col in ["temple_built", "religion_adopted", "religion_rejected"]:
    if col in ev.columns:
        s = ev[col]
        print(f"{col}: mean={s.mean():.1f}  sd={s.std():.1f}  total={s.sum():.0f}")
