"""
generate_figures.py — Thalren Vale Paper Figures
=================================================
Generates publication-quality figures from results.csv and run_event_summary.csv.

Usage:
    python generate_figures.py

Outputs PNG files to ./figures/
"""

import os
import warnings

import matplotlib
matplotlib.use("Agg")           # non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import seaborn as sns
from scipy import stats as sp_stats

warnings.filterwarnings("ignore", category=FutureWarning)

# ── Configuration ───────────────────────────────────────────────────────────
OUT_DIR = "figures"
DPI = 300
PALETTE = "muted"
sns.set_theme(style="whitegrid", palette=PALETTE, font_scale=1.1)

os.makedirs(OUT_DIR, exist_ok=True)

# ── Load data ───────────────────────────────────────────────────────────────
print("Loading results.csv …")
df = pd.read_csv("results.csv")
print(f"  {len(df):,} rows, {df['run_id'].nunique()} runs")

print("Loading run_event_summary.csv …")
ev = pd.read_csv("run_event_summary.csv")
print(f"  {len(ev):,} rows, {ev.shape[1]} columns")


# ═════════════════════════════════════════════════════════════════════════════
# Figure 1 — Population time-series (all 100 runs + ensemble median/IQR)
# ═════════════════════════════════════════════════════════════════════════════
print("\nFigure 1: Population time-series …")
fig, ax = plt.subplots(figsize=(10, 5))

# individual traces (thin, transparent)
for rid, grp in df.groupby("run_id"):
    ax.plot(grp["tick"], grp["pop"], color="steelblue", alpha=0.07, linewidth=0.4)

# ensemble statistics per tick
stats = df.groupby("tick")["pop"].agg(["median", lambda x: x.quantile(0.25),
                                        lambda x: x.quantile(0.75),
                                        lambda x: x.quantile(0.05),
                                        lambda x: x.quantile(0.95)])
stats.columns = ["median", "q25", "q75", "q05", "q95"]
ax.fill_between(stats.index, stats["q05"], stats["q95"],
                color="steelblue", alpha=0.10, label="90% range (5th–95th)")
ax.fill_between(stats.index, stats["q25"], stats["q75"],
                color="steelblue", alpha=0.25, label="IQR (25th–75th)")
ax.plot(stats.index, stats["median"], color="navy", linewidth=1.5, label="Median")

ax.set_xlabel("Tick")
ax.set_ylabel("Population")
ax.set_title("Figure 1. Population Trajectories Across 100 Runs")
ax.legend(loc="upper left")
ax.set_xlim(0, 10_000)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "fig1_population_timeseries.png"), dpi=DPI)
plt.close(fig)
print("  → fig1_population_timeseries.png")


# ═════════════════════════════════════════════════════════════════════════════
# Figure 2 — Faction count time-series (same treatment)
# ═════════════════════════════════════════════════════════════════════════════
print("Figure 2: Faction count time-series …")
fig, ax = plt.subplots(figsize=(10, 5))

for rid, grp in df.groupby("run_id"):
    ax.plot(grp["tick"], grp["factions"], color="darkorange", alpha=0.07, linewidth=0.4)

stats = df.groupby("tick")["factions"].agg(["median", lambda x: x.quantile(0.25),
                                             lambda x: x.quantile(0.75),
                                             lambda x: x.quantile(0.05),
                                             lambda x: x.quantile(0.95)])
stats.columns = ["median", "q25", "q75", "q05", "q95"]
ax.fill_between(stats.index, stats["q05"], stats["q95"],
                color="darkorange", alpha=0.10, label="90% range (5th–95th)")
ax.fill_between(stats.index, stats["q25"], stats["q75"],
                color="darkorange", alpha=0.25, label="IQR (25th–75th)")
ax.plot(stats.index, stats["median"], color="darkred", linewidth=1.5, label="Median")

ax.set_xlabel("Tick")
ax.set_ylabel("Active Factions")
ax.set_title("Figure 7. Active Faction Count Across 100 Runs")
ax.legend(loc="upper left")
ax.set_xlim(0, 10_000)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "fig7_faction_timeseries.png"), dpi=DPI)
plt.close(fig)
print("  → fig7_faction_timeseries.png")


# ═════════════════════════════════════════════════════════════════════════════
# Figure 3 — Aggregate tension time-series
# ═════════════════════════════════════════════════════════════════════════════
print("Figure 3: Tension time-series …")
fig, ax = plt.subplots(figsize=(10, 5))

# Skip individual traces — not informative on log scale; use bands only
stats = df.groupby("tick")["tension"].agg(["median", lambda x: x.quantile(0.25),
                                            lambda x: x.quantile(0.75),
                                            lambda x: x.quantile(0.05),
                                            lambda x: x.quantile(0.95)])
stats.columns = ["median", "q25", "q75", "q05", "q95"]
ax.fill_between(stats.index, stats["q05"], stats["q95"],
                color="firebrick", alpha=0.10, label="90% range (5th–95th)")
ax.fill_between(stats.index, stats["q25"], stats["q75"],
                color="firebrick", alpha=0.2, label="IQR (25th–75th)")
ax.plot(stats.index, stats["median"], color="darkred", linewidth=1.5, label="Median")

ax.set_xlabel("Tick")
ax.set_ylabel("Aggregate Tension (log scale)")
ax.set_yscale("symlog", linthresh=10)
ax.set_title("Figure 3. Aggregate Tension Across 100 Runs (Log Scale)")
ax.legend(loc="upper left")
ax.set_xlim(0, 10_000)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "fig3_tension_timeseries.png"), dpi=DPI)
plt.close(fig)
print("  → fig3_tension_timeseries.png")


# ═════════════════════════════════════════════════════════════════════════════
# Figure 4 — First-war tick distribution (histogram)
# ═════════════════════════════════════════════════════════════════════════════
print("Figure 4: First-war tick histogram …")
first_war = ev["first_war_tick"].dropna()
fig, ax = plt.subplots(figsize=(8, 4.5))
ax.hist(first_war, bins=30, color="indianred", edgecolor="white", alpha=0.85,
        density=True, label="Histogram")
# KDE overlay
from scipy.stats import gaussian_kde
_kde = gaussian_kde(first_war, bw_method='scott')
_kde_x = np.linspace(first_war.min(), first_war.max(), 200)
ax.plot(_kde_x, _kde(_kde_x), color="darkred", linewidth=2, label="KDE")
ax.axvline(first_war.median(), color="navy", linestyle="--", linewidth=1.5,
           label=f"Median = {first_war.median():.0f}")
ax.set_xlabel("Tick of First War Declaration")
ax.set_ylabel("Density")
ax.set_title("Figure 4. Distribution of First War Onset (n = 100)")
ax.legend()
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "fig4_first_war_histogram.png"), dpi=DPI)
plt.close(fig)
print("  → fig4_first_war_histogram.png")


# ═════════════════════════════════════════════════════════════════════════════
# Figure 5 — War and disruption event counts per run (box/strip)
# ═════════════════════════════════════════════════════════════════════════════
print("Figure 5: War & disruption box plots …")
war_cols = ["war_declared", "war_ends", "treaty_formed", "treaty_broken"]
dis_cols = ["plague", "civil_war", "great_migration"]

melt_war = ev[war_cols].melt(var_name="Event", value_name="Count")
melt_dis = ev[dis_cols].melt(var_name="Event", value_name="Count")

fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=False)

# Panel A — War events
sns.boxplot(data=melt_war, x="Event", y="Count", ax=axes[0],
            palette="Reds", fliersize=2)
sns.stripplot(data=melt_war, x="Event", y="Count", ax=axes[0],
              color="black", alpha=0.15, size=2.5, jitter=True)
axes[0].set_title("A. War & Diplomacy Events per Run")
axes[0].set_xlabel("")
axes[0].tick_params(axis="x", rotation=25)

# Panel B — Disruption events
sns.boxplot(data=melt_dis, x="Event", y="Count", ax=axes[1],
            palette="Purples", fliersize=2)
sns.stripplot(data=melt_dis, x="Event", y="Count", ax=axes[1],
              color="black", alpha=0.15, size=2.5, jitter=True)
axes[1].set_title("B. Stochastic Disruptions per Run")
axes[1].set_xlabel("")
axes[1].tick_params(axis="x", rotation=25)

fig.suptitle("Figure 5. Per-Run Event Counts (n = 100)", fontsize=13, y=1.02)
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "fig5_event_boxplots.png"), dpi=DPI,
            bbox_inches="tight")
plt.close(fig)
print("  → fig5_event_boxplots.png")


# ═════════════════════════════════════════════════════════════════════════════
# Figure 6 — Technology branch counts (martial / civic / economic)
# ═════════════════════════════════════════════════════════════════════════════
print("Figure 6: Technology branch distributions …")
tech_cols = ["tech_martial", "tech_civic", "tech_industrial"]
tech_labels = {"tech_martial": "Martial", "tech_civic": "Civic",
               "tech_industrial": "Industrial"}

melt_tech = ev[tech_cols].rename(columns=tech_labels).melt(
    var_name="Branch", value_name="Count")

fig, ax = plt.subplots(figsize=(7, 4.5))
sns.violinplot(data=melt_tech, x="Branch", y="Count", palette="Set2",
               inner="box", cut=0, ax=ax)
ax.set_title("Figure 6. Technology Discoveries per Run by Branch (n = 100)")
ax.set_xlabel("")
ax.set_ylabel("Discoveries per Run")
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "fig6_tech_branches.png"), dpi=DPI)
plt.close(fig)
print("  → fig6_tech_branches.png")


# ═════════════════════════════════════════════════════════════════════════════
# Figure 7 — Era distribution (pie chart)
# ═════════════════════════════════════════════════════════════════════════════
print("Figure 7: Era distribution …")
era_cols = [c for c in ev.columns if c.startswith("era_the_")]
era_nice = {c: c.replace("era_the_", "").replace("_", " ").title() for c in era_cols}

era_data = ev[era_cols].rename(columns=era_nice)
era_means = era_data.mean().sort_values(ascending=False)
# Drop eras with negligible share for readability
era_means = era_means[era_means > 0]

fig, ax = plt.subplots(figsize=(9, 7))
colors = sns.color_palette("coolwarm", len(era_means))
wedges, texts, autotexts = ax.pie(
    era_means.values, autopct='%1.1f%%',
    colors=colors, startangle=140, pctdistance=0.8,
    wedgeprops=dict(edgecolor='white', linewidth=1.5))
for t in autotexts:
    t.set_fontsize(8)

# Draw leader lines from each wedge to a label placed well outside
# First, compute raw label positions
_label_info = []
for i, wedge in enumerate(wedges):
    ang = (wedge.theta2 - wedge.theta1) / 2.0 + wedge.theta1
    y = np.sin(np.deg2rad(ang))
    x = np.cos(np.deg2rad(ang))
    side = "right" if x >= 0 else "left"
    _label_info.append({
        'i': i, 'ang': ang, 'x': x, 'y': y, 'side': side,
        'text_x': 1.35 * np.sign(x), 'text_y': 1.4 * y,
    })

# Spread overlapping labels: sort each side by text_y, enforce minimum gap
_MIN_GAP = 0.22
for side in ("right", "left"):
    group = sorted([li for li in _label_info if li['side'] == side],
                   key=lambda li: li['text_y'])
    for j in range(1, len(group)):
        if group[j]['text_y'] - group[j-1]['text_y'] < _MIN_GAP:
            group[j]['text_y'] = group[j-1]['text_y'] + _MIN_GAP
    # Re-centre the group so it doesn't drift too far up
    if group:
        mid_shift = (group[0]['text_y'] + group[-1]['text_y']) / 2
        orig_mid = (min(li['text_y'] for li in group if li['side'] == side)
                    + max(li['text_y'] for li in group if li['side'] == side)) / 2
        # only re-centre if the shift is noticeable
        if abs(mid_shift) > 1.5:
            adj = mid_shift - np.sign(mid_shift) * 1.2
            for li in group:
                li['text_y'] -= adj

bbox_props = dict(boxstyle="square,pad=0.3", fc="white", ec="0.7", lw=0.5)
for li in _label_info:
    horiz = "left" if li['side'] == "right" else "right"
    connstyle = f"angle,angleA=0,angleB={li['ang']}"
    ax.annotate(era_means.index[li['i']], xy=(li['x'], li['y']),
                xytext=(li['text_x'], li['text_y']),
                horizontalalignment=horiz, va="center", fontsize=9,
                bbox=bbox_props,
                arrowprops=dict(arrowstyle="-", color="0.4", lw=0.8,
                                connectionstyle=connstyle),
                zorder=0)

ax.set_title("Figure 8. Mean Time Spent per Named Era Across 100 Runs", fontsize=12)

fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "fig8_era_distribution.png"), dpi=DPI,
            bbox_inches="tight")
plt.close(fig)
print("  → fig8_era_distribution.png")


# ═════════════════════════════════════════════════════════════════════════════
# Figure 8 — Battle death fraction vs war count (scatter)
# ═════════════════════════════════════════════════════════════════════════════
print("Figure 8: Battle death fraction vs wars …")
fig, ax = plt.subplots(figsize=(7, 5))
scatter_data = ev.dropna(subset=["battle_death_fraction", "war_declared"])
ax.scatter(scatter_data["war_declared"], scatter_data["battle_death_fraction"],
           alpha=0.5, s=30, color="crimson", edgecolors="white", linewidths=0.3)

# trend line with 95% confidence interval
_x = scatter_data["war_declared"].values
_y = scatter_data["battle_death_fraction"].values
slope, intercept, r_val, p_val, std_err = sp_stats.linregress(_x, _y)
x_line = np.linspace(_x.min(), _x.max(), 200)
y_line = slope * x_line + intercept
ax.plot(x_line, y_line, "--", color="navy", linewidth=1.2, label="Linear trend")
# 95% CI band
_n = len(_x)
_x_mean = _x.mean()
_se_line = std_err * np.sqrt(1/_n + (x_line - _x_mean)**2 / np.sum((_x - _x_mean)**2))
_t_crit = sp_stats.t.ppf(0.975, _n - 2)
ax.fill_between(x_line, y_line - _t_crit * _se_line, y_line + _t_crit * _se_line,
                color="navy", alpha=0.12, label="95% CI")
# Annotate r and p
ax.text(0.05, 0.95, f"r = {r_val:.3f}, p = {p_val:.2e}",
        transform=ax.transAxes, fontsize=10, verticalalignment='top',
        bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.8))

ax.set_xlabel("Wars Declared")
ax.set_ylabel("Battle Death Fraction (of total deaths)")
ax.set_title("Figure 2. Battle Lethality vs. War Frequency (n = 100)")
ax.legend()
fig.tight_layout()
fig.savefig(os.path.join(OUT_DIR, "fig2_death_fraction_vs_wars.png"), dpi=DPI)
plt.close(fig)
print("  → fig2_death_fraction_vs_wars.png")


# ═════════════════════════════════════════════════════════════════════════════
# Summary
# ═════════════════════════════════════════════════════════════════════════════
figs = [f for f in os.listdir(OUT_DIR) if f.endswith(".png")]
print(f"\nDone — {len(figs)} figures saved to ./{OUT_DIR}/")
for f in sorted(figs):
    size_kb = os.path.getsize(os.path.join(OUT_DIR, f)) / 1024
    print(f"  {f}  ({size_kb:.0f} KB)")
