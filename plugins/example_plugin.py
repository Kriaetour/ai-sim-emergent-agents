# (c) 2026 (KriaetvAspie / AspieTheBard)
# Licensed under the Polyform Noncommercial License 1.0.0
"""
example_plugin.py — Two fully-commented sample plugins for Thalren Vale.

Copy this file, rename it, and modify the class body.  The engine
discovers every ThalrenPlugin subclass in this directory automatically.

To disable a plugin without deleting it, rename the file so it does not
end in .py, or remove the ThalrenPlugin inheritance from the class.
"""

from __future__ import annotations

# The plugin_api module is importable because sim.py inserts the package
# root into sys.path before scanning the plugins/ directory.
from thalren_vale.plugin_api import (
    ThalrenPlugin,
    SimulationBridge,
    PluginCommand,
    SpawnInhabitants,
    AdjustResource,
)
from typing import List


# ══════════════════════════════════════════════════════════════════════════
# Plugin 1 — Emergency Resettlement
# Fires when total population drops below 8 regardless of why.
# Spawns a small group of survivors on the most central habitable tile.
# ══════════════════════════════════════════════════════════════════════════

class EmergencyResettlement(ThalrenPlugin):
    """Inject survivors whenever the civilisation is on the verge of collapse.

    Attributes
    ──────────
    name          : logged as 'emergency_resettlement' in event messages.
    tick_interval : checked every 20 ticks to avoid rapid-fire triggering.
    MIN_POP       : population threshold below which the event fires.
    SPAWN_COUNT   : number of new inhabitants to inject per event.
    """

    name          = "emergency_resettlement"
    tick_interval = 20        # evaluated on every 20th tick

    MIN_POP     = 8
    SPAWN_COUNT = 6

    def on_trigger(self, bridge: SimulationBridge) -> bool:
        """Fire only when the population is critically low."""
        return bridge.total_population < self.MIN_POP

    def execute(self, bridge: SimulationBridge) -> List[PluginCommand]:
        """Pick the most central habitable tile and spawn survivors there."""
        tiles = bridge.habitable_tiles
        if not tiles:
            return []   # absolutely no habitable land — nothing to do

        # Choose tile closest to grid centre
        mid = bridge.grid_size / 2
        best = min(tiles, key=lambda rc: abs(rc[0] - mid) + abs(rc[1] - mid))

        return [SpawnInhabitants(count=self.SPAWN_COUNT, location=best)]


# ══════════════════════════════════════════════════════════════════════════
# Plugin 2 — Forest Bloom
# Every 100 ticks, if there are at least 2 active factions and the world
# has been alive long enough, replenish food across all forest tiles to
# simulate a good harvest season independent of the normal regen cycle.
# ══════════════════════════════════════════════════════════════════════════

class ForestBloom(ThalrenPlugin):
    """Periodic forest food bonus — fires when multiple factions are thriving.

    This plugin demonstrates the ``AdjustResource`` command with a biome
    target rather than a specific tile.  The engine will apply the delta to
    every 'forest' tile, clamped to BIOME_MAX['forest']['food'].
    """

    name          = "forest_bloom"
    tick_interval = 100       # evaluated every 100 ticks

    FOOD_BONUS       = 12     # food units added to every forest tile
    MIN_FACTIONS     = 2      # at least this many active factions required
    MIN_TICK         = 100    # don't fire in the very early game

    def on_trigger(self, bridge: SimulationBridge) -> bool:
        return (
            bridge.current_tick >= self.MIN_TICK
            and len(bridge.active_factions) >= self.MIN_FACTIONS
        )

    def execute(self, bridge: SimulationBridge) -> List[PluginCommand]:
        return [
            AdjustResource(
                target='forest',
                resource='food',
                amount=self.FOOD_BONUS,
            )
        ]
