# (c) 2026 (KriaetvAspie / AspieTheBard)
# Licensed under the Polyform Noncommercial License 1.0.0
"""
plugin_api.py — Controlled Plugin API for Thalren Vale.

Provides a safe, sandboxed interface for user-created event plugins.

Architecture
────────────
  SimulationBridge  — read-only snapshot of core simulation state.
  ThalrenPlugin     — abstract base class all plugins must subclass.
  PluginCommand     — sealed command hierarchy; the only way a plugin can
                      mutate the world.  The engine validates and executes
                      each command, enforcing safety invariants (e.g. POP_CAP).

Supported commands
──────────────────
  SpawnInhabitants(count, location)
      Inject up to *count* new inhabitants at a given (r, c) tile, subject to
      POP_CAP and tile habitability.

  AdjustResource(biome_or_tile, resource, amount)
      Nudge a resource value on one tile or across all tiles of a biome.
      Clamped to [0, BIOME_MAX] so plugins cannot over-fill or deplete to
      negative values.

Usage summary
─────────────
  1. Create a .py file inside the ``plugins/`` directory at the project root.
  2. Define a class that inherits from ThalrenPlugin.
  3. Implement ``on_trigger`` (returns True when the event should fire) and
     ``execute`` (returns a list of PluginCommand objects).
  4. The engine auto-discovers and registers the class via load_plugins().

Example
───────
  class MyPlugin(ThalrenPlugin):
      name = "my_plugin"
      tick_interval = 50       # fires every 50 ticks
      def on_trigger(self, bridge):
          return bridge.total_population < 10
      def execute(self, bridge):
          return [SpawnInhabitants(count=5, location=(0, 0))]
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    # Avoid circular imports — these are only used for type hints.
    pass


# ══════════════════════════════════════════════════════════════════════════
# SimulationBridge — read-only snapshot
# ══════════════════════════════════════════════════════════════════════════

class SimulationBridge:
    """Immutable snapshot of simulation state passed to every plugin call.

    All properties are computed once at construction time from live module
    state.  Plugins may read these freely; they cannot write back through
    this object.  Mutating the underlying lists returned by ``active_factions``
    or ``biome_map`` is undefined behaviour — treat them as read-only.
    """

    def __init__(
        self,
        *,
        tick: int,
        people: list,
        factions: list,
        world: list,
        pop_cap: int,
        biome_max: dict,
        event_log: list,
    ) -> None:
        # Snapshot scalars
        self._tick       : int   = tick
        self._pop        : int   = len(people)
        self._pop_cap    : int   = pop_cap
        self._biome_max  : dict  = biome_max   # reference; plugins must not mutate
        # Snapshot references (plugins use these read-only)
        self._people     : list  = people
        self._factions   : list  = factions
        self._world      : list  = world
        self._event_log  : list  = event_log

    # ── Read-only properties ───────────────────────────────────────────────

    @property
    def current_tick(self) -> int:
        """The tick number this bridge was created for."""
        return self._tick

    @property
    def total_population(self) -> int:
        """Current live-inhabitant count."""
        return self._pop

    @property
    def population_cap(self) -> int:
        """Hard population ceiling defined in config.POP_CAP."""
        return self._pop_cap

    @property
    def active_factions(self) -> list:
        """Factions with at least one living member (read-only list)."""
        return [f for f in self._factions if f.members]

    @property
    def faction_names(self) -> list[str]:
        """Names of all active factions."""
        return [f.name for f in self.active_factions]

    @property
    def biome_map(self) -> list:
        """The raw ``world`` grid list.  Shape: world[r][c] is a tile dict.

        Tile keys: 'biome', 'resources', 'habitable', …
        Do not modify tile dicts from within a plugin.
        """
        return self._world

    @property
    def grid_size(self) -> int:
        """Current grid side-length (world is grid_size × grid_size)."""
        return len(self._world)

    @property
    def habitable_tiles(self) -> list[tuple[int, int]]:
        """List of (r, c) coordinates that are currently habitable."""
        g = len(self._world)
        return [
            (r, c)
            for r in range(g)
            for c in range(g)
            if self._world[r][c]['habitable']
        ]

    @property
    def recent_events(self) -> list[str]:
        """Last 20 entries from the shared event log (read-only copy)."""
        return list(self._event_log[-20:])

    def tile_biome(self, r: int, c: int) -> str:
        """Return the biome string for tile (r, c)."""
        return self._world[r][c]['biome']

    def tile_resources(self, r: int, c: int) -> dict:
        """Return a *copy* of the resource dict for tile (r, c)."""
        return dict(self._world[r][c]['resources'])

    def faction_by_name(self, name: str):
        """Return the first Faction whose name matches *name*, or None."""
        for f in self._factions:
            if f.name == name:
                return f
        return None


# ══════════════════════════════════════════════════════════════════════════
# PluginCommand hierarchy — the *only* way plugins can affect the world
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class PluginCommand(abc.ABC):
    """Sealed base class.  Every concrete command must inherit from this."""

    @abc.abstractmethod
    def describe(self) -> str:
        """Human-readable description for log messages."""


@dataclass
class SpawnInhabitants(PluginCommand):
    """Request that *count* new inhabitants be spawned at *location* = (r, c).

    Safety checks applied by the engine before executing:
      - count is clamped to [1, 20] per plugin call.
      - Total population may not exceed POP_CAP.
      - location must be a habitable tile; if not, the engine picks the
        nearest habitable tile that exists.
    """
    count:    int
    location: Tuple[int, int]

    def describe(self) -> str:
        return f"SpawnInhabitants(count={self.count}, location={self.location})"


@dataclass
class AdjustResource(PluginCommand):
    """Request a delta adjustment to a resource on a specific tile or biome.

    Attributes
    ──────────
    target : (int, int) | str
        Either a tile coordinate ``(r, c)`` or a biome name string such as
        ``'forest'``.  When a biome string is provided the delta is applied
        to **every** tile of that biome.
    resource : str
        One of 'food', 'wood', 'ore', 'stone', 'water'.
    amount : int | float
        Positive to add, negative to subtract.  The engine clamps each tile
        to ``[0, BIOME_MAX[biome][resource]]``.
    """
    target:   object          # (r, c) tuple or biome string
    resource: str
    amount:   float

    def describe(self) -> str:
        return (f"AdjustResource(target={self.target!r}, "
                f"resource={self.resource!r}, amount={self.amount})")


# ══════════════════════════════════════════════════════════════════════════
# ThalrenPlugin — abstract base class for all user plugins
# ══════════════════════════════════════════════════════════════════════════

class ThalrenPlugin(abc.ABC):
    """Base class for every Thalren Vale event plugin.

    Subclass this, set ``name`` and optionally ``tick_interval``, then
    implement ``on_trigger`` and ``execute``.

    Class attributes (override in subclass)
    ──────────────────────────────────────
    name : str
        Unique identifier for logging. Defaults to the class name.
    tick_interval : int
        How often (in simulation ticks) the engine evaluates this plugin.
        Default is 10.  Set to 1 to evaluate every tick.
    """

    name:          str = ""
    tick_interval: int = 10

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.name:
            cls.name = cls.__name__

    # ── Required interface ─────────────────────────────────────────────────

    @abc.abstractmethod
    def on_trigger(self, bridge: SimulationBridge) -> bool:
        """Return True if the plugin's event should fire this tick.

        Called by the engine on every multiple-of-``tick_interval`` tick.
        Returning False skips ``execute`` entirely.  Keep this method cheap —
        it runs synchronously inside the main simulation loop.
        """

    @abc.abstractmethod
    def execute(self, bridge: SimulationBridge) -> List[PluginCommand]:
        """Return the list of commands this plugin wants to run.

        The engine validates each command before applying it.  Return an
        empty list to do nothing (e.g. if a secondary condition isn't met).
        Commands that fail validation are silently skipped and logged.
        """

    # ── Optional hooks ────────────────────────────────────────────────────

    def on_load(self) -> None:
        """Called once when the plugin is registered at simulation start."""

    def on_unload(self) -> None:
        """Called once when the simulation ends (cleanup if needed)."""
