# (c) 2026 (KriaetvAspie / AspieTheBard)
# Licensed under the Polyform Noncommercial License 1.0.0
"""
mythology.py — Layer 8 (narrative): LLM-driven chronicle, myths, and epitaphs.

Read-only observer — never modifies simulation state.

Call order each tick (after diplomacy_tick):
    mythology_tick(factions, all_dead, t, event_log)

At end of simulation:
    mythology_final_summary(factions, all_dead, ticks, event_log)

Before first tick:
    init(tee)   — pass the _LogTee object so mythology can write
                  directly to log-file + terminal without keyword filtering

Public state:
    chronicles     list[dict]           — {'start_t', 'end_t', 'text'}
    faction_myths  dict[str, list[str]] — faction_name → [myth_text, ...]
    epitaphs       dict[str, str]       — person_name  → epitaph_text
"""

import sys, re, json, textwrap, pathlib, time
from datetime import datetime
import urllib.request, urllib.error

sys.stdout.reconfigure(encoding='utf-8')

from . import config
from .beliefs import LABELS, inh_cores

# ══════════════════════════════════════════════════════════════════════════
# I/O bridge — set by sim.py so mythology bypasses the keyword filter
# ══════════════════════════════════════════════════════════════════════════

_tee_ref = None   # the _LogTee object created in sim.py


def init(tee) -> None:
    """Call once after _LogTee is created.  Stores reference for direct I/O."""
    global _tee_ref
    _tee_ref = tee


def _direct_print(text: str) -> None:
    """
    Write to BOTH the log file and the real terminal, bypassing the keyword
    filter in _LogTee.  Fallback to regular print if tee not yet initialised.
    """
    if _tee_ref is not None:
        _tee_ref._log.write(text + '\n')
        _tee_ref._log.flush()
        _tee_ref._real.write(text + '\n')
        _tee_ref._real.flush()
    else:
        print(text)


# ══════════════════════════════════════════════════════════════════════════
# Module-level state
# ══════════════════════════════════════════════════════════════════════════

chronicles:       list = []   # {'start_t', 'end_t', 'text'}
faction_myths:    dict = {}   # faction_name → [text, ...]
epitaphs:         dict = {}   # person_name  → text

_epitaphed:       set  = set()   # names already given epitaphs
_last_chr_t:      int  = 0       # tick when last chronicle was generated
_myth_last_t:     dict = {}      # faction_name → last myth-generation tick
_llm_fired:       bool = False   # set True each time _ollama() is invoked; reset by sim.py


# ══════════════════════════════════════════════════════════════════════════
# Ollama REST call
# ══════════════════════════════════════════════════════════════════════════

def _ollama(prompt: str, max_tokens: int = 200, timeout: int = None) -> str:
    """
    POST to the local Ollama API.
    Returns the response text, or '' if the call fails for any reason.
    max_tokens overrides the config default for this call only.
    timeout overrides config.OLLAMA_TIMEOUT for this call only.
    """
    _timeout = timeout if timeout is not None else config.OLLAMA_TIMEOUT
    try:
        global _llm_fired
        _llm_fired = True
        payload = json.dumps({
            "model":      config.NARRATIVE_MODEL,
            "prompt":     prompt,
            "system":     (
                "You are an ancient, nameless scribe of the void. "
                "Your language is archaic, dark, and descriptive. "
                "Never use conversational filler, greetings, or polite confirmations. "
                "Start every response directly with the chronicle text."
            ),
            "stream":     False,
            "keep_alive": 0,
            "options": {
                "temperature": 0.8,
                "num_predict": max_tokens,
                "num_ctx":     4096,
            },
        }).encode("utf-8")

        req = urllib.request.Request(
            config.OLLAMA_URL,
            data    = payload,
            headers = {"Content-Type": "application/json"},
            method  = "POST",
        )
        with urllib.request.urlopen(req, timeout=_timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("response", "").strip()

    except Exception:
        return ""


def _clean(text: str, word_limit: int) -> str:
    """
    Post-process LLM output for small-model safety:
    - Drop everything after the first blank line (rambling / meta-comments).
    - Collapse remaining newlines into spaces.
    - Remove any incomplete trailing sentence (not ending in . ! ?).
    - Hard-cap at word_limit words; trim back to last sentence boundary.
    """
    if not text:
        return text
    # Drop after double newline
    text = text.split('\n\n')[0]
    # Collapse single newlines
    text = ' '.join(text.split('\n')).strip()
    # Remove incomplete trailing sentence
    if text and text[-1] not in '.!?':
        last = max(text.rfind('.'), text.rfind('!'), text.rfind('?'))
        text = text[:last + 1] if last > 0 else ''
    # Hard word cap — trim back to last complete sentence
    words = text.split()
    if len(words) > word_limit:
        capped   = ' '.join(words[:word_limit])
        last     = max(capped.rfind('.'), capped.rfind('!'), capped.rfind('?'))
        text     = capped[:last + 1] if last > 0 else capped + '.'
    return text.strip()


_STRICT = (
    "STRICT RULES:\n"
    "- Complete every sentence. Never cut off mid-sentence.\n"
    "- End with a period. No trailing fragments.\n"
    "- Output ONLY the requested text. Nothing else."
)


def _clean_multi(text: str, word_limit: int = 500) -> str:
    """
    Like _clean but preserves paragraph breaks (double newlines).
    Used for multi-paragraph outputs such as the final summary.
    """
    if not text:
        return text
    # Collapse each paragraph individually, then re-join
    paras = [p.strip() for p in text.split('\n\n') if p.strip()]
    cleaned: list = []
    for para in paras:
        para = ' '.join(para.split('\n')).strip()
        # Remove incomplete trailing sentence within the paragraph
        if para and para[-1] not in '.!?':
            last = max(para.rfind('.'), para.rfind('!'), para.rfind('?'))
            para = para[:last + 1] if last > 0 else para + '.'
        if para:
            cleaned.append(para)
    text = '\n\n'.join(cleaned)
    # Global word cap
    words = text.split()
    if len(words) > word_limit:
        capped = ' '.join(words[:word_limit])
        last   = max(capped.rfind('.'), capped.rfind('!'), capped.rfind('?'))
        text   = capped[:last + 1] if last > 0 else capped + '.'
    return text.strip()


# ══════════════════════════════════════════════════════════════════════════
# Event-log helpers
# ══════════════════════════════════════════════════════════════════════════

_TICK_RE = re.compile(r'^Tick\s+0*(\d+):')


def _events_in_window(event_log: list, start_t: int, end_t: int) -> list:
    """Return event_log entries whose tick number falls in [start_t, end_t]."""
    out = []
    for entry in event_log:
        m = _TICK_RE.match(entry)
        if m and start_t <= int(m.group(1)) <= end_t:
            out.append(entry)
    return out


def _filter(entries: list, keywords: tuple) -> list:
    return [e for e in entries if any(k in e for k in keywords)]


# ══════════════════════════════════════════════════════════════════════════
# Display helpers
# ══════════════════════════════════════════════════════════════════════════

def _box(title: str, text: str, width: int = 72) -> None:
    border  = '═' * width
    wrapper = textwrap.TextWrapper(width=width - 4,
                                   break_long_words=False,
                                   break_on_hyphens=False)
    _direct_print('')
    _direct_print(border)
    _direct_print(f'  {title}')
    _direct_print(border)
    for para in text.split('\n'):
        for line in (wrapper.wrap(para) or ['']):
            _direct_print(f'  {line}')
    _direct_print(border)
    _direct_print('')


def _print_epitaph(name: str, text: str) -> None:
    _direct_print(f'  🪦  {name}: {text}')


def _print_myth(faction_name: str, text: str) -> None:
    wrapper = textwrap.TextWrapper(width=68)
    _direct_print(f'  📜  MYTH of {faction_name}:')
    for line in wrapper.wrap(text) or ['']:
        _direct_print(f'        {line}')


# ══════════════════════════════════════════════════════════════════════════
# Prompt-context builders
# ══════════════════════════════════════════════════════════════════════════

def _rep_label(rep: int) -> str:
    if rep >=  5: return 'honorable'
    if rep >=  2: return 'trusted'
    if rep >= -1: return 'neutral'
    if rep >= -3: return 'questionable'
    return 'disgraced'


def _faction_block(factions: list) -> str:
    try:
        from . import diplomacy as _dip
    except ImportError:
        _dip = None
    lines = []
    for f in factions:
        if not f.members:
            continue
        beliefs = ', '.join(LABELS.get(b, b) for b in f.shared_beliefs[:4])
        techs   = ', '.join(sorted(getattr(f, 'techs', set())))
        rep     = _dip.get_rep(f.name) if _dip else 0
        lbl     = _rep_label(rep)
        fallen  = ', '.join(lg['name'] for lg in f.legends[-3:])
        line    = (f'- {f.name}: {len(f.members)} members, '
                   f'beliefs=[{beliefs}], techs=[{techs}], '
                   f'rep={rep} ({lbl})'
                   + (f', fallen=[{fallen}]' if fallen else ''))
        lines.append(line)
    return '\n'.join(lines) or '(no active factions)'


def _war_block(start_t: int, end_t: int) -> str:
    try:
        from . import combat as _cbt
    except ImportError:
        return '(none)'
    lines = []
    for w in _cbt.war_history:
        if w.started_tick > end_t:
            continue
        outcome_map = {
            'surrender_d': f'{w.attacker.name} defeated {w.defender.name}',
            'surrender_a': f'{w.defender.name} repelled {w.attacker.name}',
            'ceasefire':   'bloody ceasefire',
            'exhaustion':  'exhaustion draw',
        }
        out    = outcome_map.get(w.outcome, 'ongoing')
        fallen = ', '.join(
            lg['name']
            for f in (w.all_attackers() + w.all_defenders())
            for lg in f.legends
            if start_t <= lg['tick'] <= end_t
        )
        lines.append(f'- {w.attacker.name} vs {w.defender.name}: {out}'
                     + (f' (fallen: {fallen})' if fallen else ''))
    return '\n'.join(lines) or '(none)'


def _treaty_block(start_t: int, end_t: int) -> str:
    try:
        from . import diplomacy as _dip
    except ImportError:
        return '(none)'
    lines = []
    for tr in _dip.treaty_log:
        if not (start_t <= tr.get('signed', 0) <= end_t):
            continue
        status = 'BROKEN' if tr.get('broken') else 'honored'
        lines.append(f'- {tr["a"]} & {tr["b"]}: {tr["type"]} ({status})')
    return '\n'.join(lines) or '(none)'


def _schism_block(event_log: list, start_t: int, end_t: int) -> str:
    evts = _filter(_events_in_window(event_log, start_t, end_t), ('SCHISM',))
    return '\n'.join(e.split(': ', 1)[-1] for e in evts) or '(none)'


def _tech_block(event_log: list, start_t: int, end_t: int) -> str:
    evts = _filter(_events_in_window(event_log, start_t, end_t),
                   ('TECH DISCOVERED',))
    return '\n'.join(e.split(': ', 1)[-1] for e in evts) or '(none)'


def _legend_block(factions: list, start_t: int, end_t: int) -> str:
    try:
        from . import diplomacy as _dip
    except ImportError:
        _dip = None
    lines = []
    for f in factions:
        for lg in f.legends:
            if start_t <= lg['tick'] <= end_t:
                rep  = _dip.get_rep(f.name) if _dip else 0
                role = 'hero' if rep >= 0 else 'villain'
                lines.append(f'- {lg["name"]} of {f.name} '
                              f'(tick {lg["tick"]}, {role})')
    return '\n'.join(lines) or '(none)'


# ══════════════════════════════════════════════════════════════════════════
# Rich context builders — feed specific, named events to the LLM
# ══════════════════════════════════════════════════════════════════════════

def _build_event_summary(start_t: int, end_t: int, event_log: list) -> str:
    """Compile a named, structured summary of all notable events in [start_t, end_t]."""
    try:
        from . import combat as _cbt
    except ImportError:
        _cbt = None
    try:
        from . import diplomacy as _dip
    except ImportError:
        _dip = None

    events_in = _events_in_window(event_log, start_t, end_t)
    lines     = []

    # Wars + casualties
    if _cbt:
        for w in _cbt.war_history:
            if w.started_tick > end_t:
                continue
            casualties = [
                f'{lg["name"]} of {f.name} at ({lg.get("r","?")},{lg.get("c","?")})'
                for f in (w.all_attackers() + w.all_defenders())
                for lg in f.legends
                if start_t <= lg['tick'] <= end_t
            ]
            # Also pull from event_log for coordinate data
            battle_deaths = _filter(
                _events_in_window(event_log, max(start_t, w.started_tick), end_t),
                ('fell in battle',)
            )
            if not casualties and battle_deaths:
                casualties = [e.split(': ', 1)[-1].replace('💀 ', '') for e in battle_deaths]
            outcome_map = {
                'surrender_d': f'{w.attacker.name} DEFEATED {w.defender.name} — vassalized',
                'surrender_a': f'{w.defender.name} REPELLED {w.attacker.name} — invaders driven back',
                'ceasefire':   f'{w.attacker.name} and {w.defender.name} — bloody ceasefire',
                'exhaustion':  f'{w.attacker.name} and {w.defender.name} — exhaustion stalemate',
            }
            result = outcome_map.get(w.outcome, f'{w.attacker.name} vs {w.defender.name} ongoing')
            lines.append(f'- WAR: {result}')
            if casualties:
                lines.append(f'  Casualties: {chr(10).join("    " + c for c in casualties[:6])}')

    # Treaties
    if _dip:
        for tr in _dip.treaty_log:
            if not (start_t <= tr.get('signed', 0) <= end_t):
                continue
            status = ' — later BROKEN (betrayal)' if tr.get('broken') else ''
            lines.append(f'- TREATY: {tr["a"]} and {tr["b"]} signed {tr["type"]}{status}')
        # Broken treaties in this window (even if signed earlier)
        for tr in _dip.treaty_log:
            broken_t = tr.get('broken_tick', 0)
            if broken_t and start_t <= broken_t <= end_t and tr.get('signed', 0) < start_t:
                lines.append(f'- BETRAYAL: {tr["a"]} broke pact with {tr["b"]} ({tr["type"]})')

    # Schisms
    for e in _filter(events_in, ('SCHISM',)):
        lines.append(f'- SCHISM: {e.split(": ", 1)[-1]}')

    # Tech discoveries
    for e in _filter(events_in, ('TECH DISCOVERED',)):
        lines.append(f'- DISCOVERY: {e.split(": ", 1)[-1]}')

    # Starvation deaths
    for e in _filter(events_in, ('starved',)):
        lines.append(f'- DEATH: {e.split(": ", 1)[-1]}')

    # Famine / scarcity
    for e in _filter(events_in, ('shortage',)):
        lines.append(f'- FAMINE: {e.split(": ", 1)[-1]}')

    # Humanitarian food sharing
    for e in _filter(events_in, ('shares food',))[:3]:
        lines.append(f'- MERCY: {e.split(": ", 1)[-1]}')

    # Travelers / migrants
    for e in _filter(events_in, ('Travelers',))[:2]:
        lines.append(f'- MIGRATION: {e.split(": ", 1)[-1]}')

    # Faction formations & merges
    for e in _filter(events_in, ('FACTION FORMED', 'FACTION MERGE')):
        lines.append(f'- {e.split(": ", 1)[-1]}')

    total_battle    = len(_filter(events_in, ('fell in battle',)))
    total_starve    = len(_filter(events_in, ('starved',)))
    if total_battle or total_starve:
        lines.append(f'- TOTAL DEATHS: {total_battle} in battle, {total_starve} from starvation')

    return '\n'.join(lines) if lines else '(a quiet age — no wars, no deaths, no schisms)'


def _top_events(start_t: int, end_t: int, event_log: list, n: int = 5) -> str:
    """Return the n most dramatic events in [start_t, end_t] as a short bullet list."""
    full = _build_event_summary(start_t, end_t, event_log)
    if full.startswith('('):
        return full
    # Priority order: WAR > BETRAYAL > SCHISM > DEATH > DISCOVERY > rest
    priority = ('WAR', 'BETRAYAL', 'SCHISM', 'DEATH', 'TOTAL DEATHS',
                'DISCOVERY', 'TREATY', 'MIGRATION', 'MERCY', 'FAMINE')
    buckets: dict = {k: [] for k in priority}
    buckets['OTHER'] = []
    for line in full.splitlines():
        for p in priority:
            if line.lstrip('- ').startswith(p):
                buckets[p].append(line)
                break
        else:
            buckets['OTHER'].append(line)
    ordered: list = []
    for p in priority:
        ordered.extend(buckets[p])
    ordered.extend(buckets['OTHER'])
    return '\n'.join(ordered[:n])


def _build_faction_history(f, event_log: list) -> str:
    """Build a named, specific history of one faction for myth generation."""
    try:
        from . import combat as _cbt
    except ImportError:
        _cbt = None
    try:
        from . import diplomacy as _dip
    except ImportError:
        _dip = None
    try:
        from .world import world as _world
    except ImportError:
        _world = None

    lines = []

    # Founded
    founded = getattr(f, 'founded_tick', getattr(f, 'founded', 0))
    if founded:
        lines.append(f'Founded at tick {founded}')

    # Biomes
    biomes: set = set()
    if _world:
        for (r, c) in f.territory:
            try:
                biomes.add(_world[r][c].get('biome', 'unknown'))
            except Exception:
                pass
    if biomes:
        lines.append(f'Dwells in: {" and ".join(biomes)}')

    # Wars this faction fought
    if _cbt:
        for w in _cbt.war_history:
            atk_names = [fa.name for fa in w.all_attackers()]
            def_names = [fa.name for fa in w.all_defenders()]
            if f.name in atk_names:
                outcome_map = {
                    'surrender_d': f'VICTORY — conquered {w.defender.name}',
                    'surrender_a': f'DEFEAT — driven back by {w.defender.name}',
                    'ceasefire':   f'ceasefire with {w.defender.name}',
                    'exhaustion':  f'stalemate with {w.defender.name}',
                }
                lines.append(f'War: {outcome_map.get(w.outcome, "fought " + w.defender.name)}')
            elif f.name in def_names:
                outcome_map = {
                    'surrender_d': f'VASSALIZED by {w.attacker.name}',
                    'surrender_a': f'DEFENDED against {w.attacker.name}\'s invasion',
                    'ceasefire':   f'ceasefire with {w.attacker.name}',
                    'exhaustion':  f'stalemate with {w.attacker.name}',
                }
                lines.append(f'War: {outcome_map.get(w.outcome, "defended against " + w.attacker.name)}')

    # Fallen members
    for lg in f.legends:
        lines.append(f'Fallen: {lg["name"]} (tick {lg["tick"]})')

    # Allies & betrayals
    if _dip:
        allies    = set()
        betrayed  = []
        backstabs = []
        for tr in _dip.treaty_log:
            involves_f = (tr['a'] == f.name or tr['b'] == f.name)
            if not involves_f:
                continue
            other = tr['b'] if tr['a'] == f.name else tr['a']
            if not tr.get('broken'):
                allies.add(other)
            else:
                breaker = tr.get('broken_by', '')
                if breaker == f.name:
                    backstabs.append(other)
                else:
                    betrayed.append(other)
        if allies:
            lines.append(f'Allied with: {", ".join(allies)}')
        if betrayed:
            lines.append(f'Betrayed by: {", ".join(betrayed)}')
        if backstabs:
            lines.append(f'Broke faith with: {", ".join(backstabs)}')
        rep = _dip.get_rep(f.name)
        lines.append(f'Reputation: {rep} ({_rep_label(rep)})')

    # Techs
    techs = sorted(getattr(f, 'techs', set()))
    if techs:
        lines.append(f'Discovered: {", ".join(techs)}')

    return '\n'.join(lines) if lines else '(new faction, no history yet)'


# ══════════════════════════════════════════════════════════════════════════
# Chronicle — every 50 ticks
# ══════════════════════════════════════════════════════════════════════════

def _generate_chronicle(factions: list, t: int, event_log: list) -> None:
    global _last_chr_t
    start_t     = _last_chr_t + 1
    end_t       = t
    _last_chr_t = t

    top5      = _top_events(start_t, end_t, event_log, n=5)

    prompt = (
        "Here is a list of events from a tribal simulation. "
        "Summarize them in three grim sentences. "
        "Do not use greetings or modern terms. "
        f"Events: {top5}."
    )

    text = _clean(_ollama(prompt, max_tokens=120), word_limit=80)
    if not text:
        # Specific fallback using real data
        war_text = _war_block(start_t, end_t)
        leg_text = _legend_block(factions, start_t, end_t)
        fac_names = ', '.join(f.name for f in factions[:4] if f.members)
        parts = [f'In the age spanning tick {start_t} to {end_t}']
        if war_text != '(none)':
            parts.append('war claimed the land — ' + war_text.replace('- ', '', 1))
        if leg_text != '(none)':
            parts.append('these names were carved into memory: ' + leg_text.replace('- ', '', 1))
        if len(parts) == 1 and fac_names:
            parts.append(f'the peoples of {fac_names} endured in fragile peace')
        text = '. '.join(parts) + '.'

    chronicles.append({'start_t': start_t, 'end_t': end_t, 'text': text})
    _box(f'THE CHRONICLE — Age of Ticks {start_t}–{end_t}', text)


# ══════════════════════════════════════════════════════════════════════════
# Faction myths — every 100 ticks
# ══════════════════════════════════════════════════════════════════════════

def _generate_faction_myths(factions: list, t: int, event_log: list) -> None:
    try:
        from .world import world as _world
    except ImportError:
        _world = None

    try:
        from . import diplomacy as _dip
    except ImportError:
        _dip = None

    for f in factions:
        if not f.members:
            continue
        if t - _myth_last_t.get(f.name, 0) < 100:
            continue
        _myth_last_t[f.name] = t

        # Territory biomes
        biomes: set = set()
        if _world:
            for (r, c) in f.territory:
                try:
                    biomes.add(_world[r][c].get('biome', 'unknown'))
                except Exception:
                    pass
        beliefs_str = ', '.join(LABELS.get(b, b) for b in f.shared_beliefs[:3])
        history_str = _build_faction_history(f, event_log)
        # Trim history to essentials: first 6 lines
        hist_short  = '\n'.join(history_str.splitlines()[:6])

        prompt = (
            f"Elder of {f.name}. Beliefs: {beliefs_str}.\n"
            f"History: {hist_short}\n\n"
            "Write exactly 2 sentences. "
            "Sentence 1: your origin and what shaped your people. "
            "Sentence 2: a prophecy for your future. "
            "Name real factions or fallen from the history. Under 50 words.\n"
            + _STRICT
        )

        text = _clean(_ollama(prompt, max_tokens=75), word_limit=50)
        if not text:
            # Build a specific fallback from actual data
            fallen = ', '.join(lg['name'] for lg in f.legends)
            techs  = ', '.join(sorted(getattr(f, 'techs', set())))
            text = (
                f"The elders of {f.name} teach: "
                + (f"We remember the fallen — {fallen}. " if fallen else '')
                + (f"The gods rewarded our endurance with the gift of {techs.split(',')[0]}. " if techs else '')
                + f"We who hold {beliefs_str.split(',')[0] if beliefs_str else 'our truth'} sacred shall endure."
            )

        if f.name not in faction_myths:
            faction_myths[f.name] = []
        faction_myths[f.name].append(text)
        _print_myth(f.name, text)

        # Writing tech: myths spread to trade-route partners
        if 'writing' in getattr(f, 'techs', set()):
            try:
                from . import economy as _eco
                for rk in _eco.trade_routes:
                    if f.name not in rk:
                        continue
                    if not _eco.trade_routes[rk].get('active'):
                        continue
                    partner = next(n for n in rk if n != f.name)
                    if partner not in faction_myths:
                        faction_myths[partner] = []
                    if text not in faction_myths[partner]:
                        faction_myths[partner].append(
                            f'[Transmitted from {f.name}] {text}')
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════
# Epitaphs — generated at tick of battle death
# ══════════════════════════════════════════════════════════════════════════

_EPITAPH_PAT = re.compile(
    r'💀\s+(\w+)\s+\(([^)]+)\)\s+fell in battle at \((\d+),(\d+)\)'
)


def _generate_epitaphs(factions: list, all_dead: list,
                       t: int, event_log: list) -> None:
    """Check event_log for battle deaths at tick t and generate epitaphs."""
    recent_battle = _filter(_events_in_window(event_log, t, t),
                            ('fell in battle',))
    if not recent_battle:
        return

    try:
        from . import diplomacy as _dip
    except ImportError:
        _dip = None

    for entry in recent_battle:
        m = _EPITAPH_PAT.search(entry)
        if not m:
            continue
        name         = m.group(1)
        faction_name = m.group(2)
        r_s, c_s     = m.group(3), m.group(4)

        if name in _epitaphed:
            continue
        _epitaphed.add(name)

        # Gather what we know about the person
        person  = next((p for p in all_dead if p.name == name), None)
        beliefs = ''
        trust   = 0
        if person:
            beliefs = ', '.join(
                LABELS.get(b.split(':')[-1], b) for b in person.beliefs
            )
            trust = sum(person.trust.values()) if person.trust else 0

        belief1 = (beliefs.split(',')[0].strip() if beliefs else 'duty')

        prompt = (
            f"Stone epitaph for {name} of {faction_name}, fallen at ({r_s},{c_s}). "
            f"Believed in: {belief1}.\n"
            "Write exactly 1 sentence under 15 words. "
            f"Format: Here lies {name}, who [deed].\n"
            + _STRICT
        )

        text = _clean(_ollama(prompt, max_tokens=30), word_limit=15)
        if not text:
            text = f"Here lies {name} of {faction_name}, who fell defending {belief1}."

        epitaphs[name] = text
        _print_epitaph(name, text)


# ══════════════════════════════════════════════════════════════════════════
# Main per-tick entry point
# ══════════════════════════════════════════════════════════════════════════

def mythology_tick(factions: list, all_dead: list,
                   t: int, event_log: list) -> None:
    """Called every tick from sim.py. Dispatches generators as appropriate."""
    if not config.MYTHOLOGY_ENABLED:
        return

    active = [f for f in factions if f.members]

    # ── Epitaphs: every tick for any new battle deaths ─────────────────────
    _generate_epitaphs(active, all_dead, t, event_log)

    # ── Chronicle: every 50 ticks ─────────────────────────────────────────
    if t % 50 == 0:
        _generate_chronicle(active, t, event_log)

    # ── Faction myths: every 100 ticks ────────────────────────────────────
    if t % 100 == 0:
        _generate_faction_myths(active, t, event_log)


# ══════════════════════════════════════════════════════════════════════════
# Structured era summary builder (for final LLM prompt)
# ══════════════════════════════════════════════════════════════════════════

_FALLEN_RE = re.compile(r'💀\s+(\w+)')


def _build_structured_summary(event_log: list, ticks: int,
                               era_summaries: list) -> str:
    """
    Build a structured, era-by-era text summary for the final LLM prompt.
    Never passes raw event_log; extracts named, specific facts per era.
    """
    try:
        from . import combat as _cbt
    except ImportError:
        _cbt = None

    lines: list = []
    n_eras = max(1, (ticks + 99) // 100)

    for era_n in range(1, n_eras + 1):
        start_t = (era_n - 1) * 100 + 1
        end_t   = min(era_n * 100, ticks)
        entries = _events_in_window(event_log, start_t, end_t)

        archived = next((s for s in era_summaries if s['start_t'] == start_t), None)
        era_lbl  = archived['name'] if archived else None

        wars_n   = len(_filter(entries, ('WAR DECLARED',)))
        battle_n = len(_filter(entries, ('fell in battle',)))
        starve_n = len(_filter(entries, ('starved',)))
        schism_n = len(_filter(entries, ('SCHISM',)))
        merge_n  = len(_filter(entries, ('FACTION MERGE',)))
        forming  = len(_filter(entries, ('FACTION FORMED',)))
        disrupt  = _filter(entries, ('GREAT MIGRATION', 'PLAGUE SWEEPS',
                                     'CIVIL WAR', 'PROMISED LAND', 'PROPHET',
                                     'WORLD EVENT'))

        tech_entries = _filter(entries, ('TECH DISCOVERED',))
        tech_names   = [e.split('TECH DISCOVERED:')[-1].strip()
                        for e in tech_entries][:2]

        fallen_names: list = []
        for e in _filter(entries, ('fell in battle', 'starved', 'wasted away'))[:6]:
            m = _FALLEN_RE.search(e) or re.search(r'(\w+)\s+starved', e)
            if m and m.group(1) not in fallen_names:
                fallen_names.append(m.group(1))
                if len(fallen_names) >= 4:
                    break

        war_names: list = []
        if _cbt:
            for w in _cbt.war_history:
                if start_t <= w.started_tick <= end_t:
                    lbl = {'surrender_d': 'victory', 'surrender_a': 'repelled',
                           'ceasefire': 'ceasefire', 'exhaustion': 'stalemate'}
                    war_names.append(f'{w.attacker.name} vs {w.defender.name}'
                                     f' ({lbl.get(w.outcome, "ongoing")})')

        disrupt_lbl = ''
        if disrupt:
            disrupt_lbl = disrupt[0].split('\u2014')[-1].strip().rstrip(')')[:60]

        if not era_lbl:
            joined = ' '.join(entries)
            if 'PLAGUE' in joined:          era_lbl = 'Age of Sickness'
            elif wars_n >= 3 or battle_n >= 8: era_lbl = 'Crimson Years'
            elif wars_n >= 1:               era_lbl = 'Age of Conflict'
            elif starve_n >= 5:             era_lbl = 'Great Famine'
            elif len(tech_names) >= 2:      era_lbl = 'Age of Discovery'
            else:                           era_lbl = 'Long Peace'

        parts = [f'Era {era_n} (ticks {start_t}-{end_t}, {era_lbl}):']
        if forming:
            parts.append(f'{forming} faction(s) founded.')
        if war_names:
            parts.append('Wars: ' + '; '.join(war_names[:2]) + '.')
        elif wars_n:
            parts.append(f'{wars_n} war(s).')
        total_dead = battle_n + starve_n
        if total_dead:
            fell = (', '.join(fallen_names[:3]) + ' and others' if len(fallen_names) > 3
                    else ', '.join(fallen_names))
            parts.append(f'{total_dead} dead' + (f' ({fell})' if fell else '') + '.')
        if schism_n:
            parts.append(f'{schism_n} schism(s).')
        if merge_n:
            parts.append(f'{merge_n} merger(s).')
        if tech_names:
            parts.append(f'Discovered: {", ".join(tech_names)}.')
        if disrupt_lbl:
            parts.append(disrupt_lbl + '.')
        if len(parts) == 1:
            parts.append('Quiet age -- no wars, no deaths.')

        lines.append(' '.join(parts))

    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════════════════
# Final narrative summary — called from sim.py's finally block
# ══════════════════════════════════════════════════════════════════════════

def mythology_final_summary(factions: list, all_dead: list,
                             ticks: int, event_log: list,
                             era_summaries: list = None) -> None:
    """Generate and print the full epic history at end of simulation."""
    if not config.MYTHOLOGY_ENABLED:
        return
    if era_summaries is None:
        era_summaries = []

    try:
        from . import combat as _cbt
        war_count = len(_cbt.war_history)
    except Exception:
        _cbt = None
        war_count = 0

    active  = [f for f in factions if f.members]
    defunct = [f for f in factions if not f.members]
    tot_dead = len(all_dead)

    leg_names = [f'{lg["name"]} of {f.name}'
                 for f in factions for lg in f.legends][:6]

    # ── Build source text from already-generated era chronicles (prose, not data) ─
    _chr_texts   = [c['text'] for c in chronicles] if chronicles else []
    _chr_joined  = '\n\n'.join(_chr_texts)
    if len(_chr_joined) > 1500:
        _chr_joined = '...' + _chr_joined[-1500:]
    if not _chr_joined:
        _chr_joined = '(No chronicles recorded.)'

    # ── Final summary stats ────────────────────────────────────────────────
    alive        = sum(len(f.members) for f in active)
    total        = alive + tot_dead
    active_names = ', '.join(f.name for f in active) or 'none'
    dead_count   = len(defunct)
    legend_count = len(leg_names)

    prompt = f"""Write a cohesive, 4-paragraph epic history of this civilization in the style of Tolkien's Silmarillion — grand, poetic, tragic.

CHRONICLES (source material — weave these into your narrative):
{_chr_joined}

FINAL STATE:
Survivors: {alive}/{total}
Active factions: {active_names}
Fallen factions: {dead_count}
Legends fallen: {legend_count}

STRICT FORMAT — each paragraph must begin with EXACTLY these phrases:
Paragraph 1: "Before the first winter..."
Paragraph 2: "Yet peace is a fragile thing..."
Paragraph 3: "The great wars came..."
Paragraph 4: "Now only..."

RULES:
- Exactly 4 paragraphs, each 3–5 sentences.
- AVOID REPETITION. Do not start sentences with "The [Faction]" repeatedly.
- Name specific factions and fallen warriors from the chronicles.
- Flowing prose only. No bullet points, no lists.
- Complete every sentence. End each paragraph with a period.
- Under 400 words total."""

    text = _clean_multi(_ollama(prompt, max_tokens=500, timeout=120), word_limit=500)

    # ── Post-processing: sanitise LLM output ────────────────────────────
    if text:
        _lines: list = []
        for _ln in text.splitlines():
            _stripped = _ln.strip()
            # Drop forbidden openers
            if _stripped.lower().startswith('in those days'):
                continue
            if _stripped.lower().startswith('it was recorded'):
                continue
            # Strip leading bullet / dash characters
            _stripped = re.sub(r'^[\-\*\•·]+\s*', '', _stripped)
            _lines.append(_stripped)
        text = '\n'.join(_lines).strip()
        # Re-merge paragraph breaks from consecutive blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Trim to 500 words (preserve paragraph boundaries where possible)
        _words = text.split()
        if len(_words) > 500:
            text = ' '.join(_words[:500])
        # Thin output warning
        if len(text.split()) < 100:
            text = '[The chronicler\'s vision was unclear. The ages spoke thus:]\n\n' + text

    if not text:
        # Fallback: 4 paragraphs using canonical openers + last era chronicles
        _last3 = [c['text'] for c in chronicles[-3:]] if chronicles else []
        if len(_last3) >= 3:
            text = (
                f'Before the first winter, {_last3[0]}\n\n'
                f'Yet peace is a fragile thing — {_last3[1]}\n\n'
                f'The great wars came, and the land bore witness: {_last3[2]}\n\n'
                f'Now only {active_names} endure. '
                f'{tot_dead} souls perished across {ticks} ticks of mortal struggle, '
                f'and {dead_count} faction{"s" if dead_count != 1 else ""} passed into memory.'
            )
        elif _last3:
            body = ' '.join(_last3)
            text = (
                f'Before the first winter, the peoples of this world first gathered and made their homes.\n\n'
                f'Yet peace is a fragile thing — {body}\n\n'
                f'The great wars came: {war_count} conflict{"s" if war_count != 1 else ""} were fought across the land.\n\n'
                f'Now only {active_names} endure. {tot_dead} souls perished in total.'
            )
        else:
            text = (
                f'Before the first winter, the peoples of this world first gathered and made their homes.\n\n'
                f'Yet peace is a fragile thing — tensions rose between the factions as the ages turned.\n\n'
                f'The great wars came: {war_count} conflict{"s" if war_count != 1 else ""} were fought across {ticks} ticks.\n\n'
                f'Now only {active_names} endure. {tot_dead} souls perished in total.'
            )

    _box(f'THE GREAT HISTORY — A Chronicle of {ticks} Ticks', text, width=76)

    # ── Save final history to timestamped file ───────────────────────────
    _ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
    _filename = f'history_{_ts}.txt'
    pathlib.Path(_filename).write_text(text, encoding='utf-8')
    _direct_print(f'Saved history to {_filename}')


# ══════════════════════════════════════════════════════════════════════════
# Mythology report (called from display.final_report via display.py)
# ══════════════════════════════════════════════════════════════════════════

def mythology_report() -> None:
    """Print a compact summary of mythology output for the final report."""
    if not chronicles and not epitaphs and not faction_myths:
        return
    sep = '─' * 72
    _direct_print(f'\n{sep}')
    _direct_print(f'MYTHOLOGY SUMMARY')
    _direct_print(sep)
    _direct_print(f'  Chronicles generated : {len(chronicles)}')
    _direct_print(f'  Faction myths        : {sum(len(v) for v in faction_myths.values())}')
    _direct_print(f'  Epitaphs written     : {len(epitaphs)}')
    if epitaphs:
        _direct_print(f'\n  Epitaphs:')
        for name, text in list(epitaphs.items())[:8]:
            _direct_print(f'    🪦 {name}: {text[:80]}...' if len(text) > 80 else f'    🪦 {name}: {text}')
    _direct_print(sep)
