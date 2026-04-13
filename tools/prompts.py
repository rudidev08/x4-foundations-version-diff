"""Prompt templates + topic definitions for the diff pipeline.

Prompts are piped to LLM_CLI via stdin; output is read from stdout. Analyze
prompts point the LLM at the local source trees (`source/{v1}/`, `source/{v2}/`)
so it can read surrounding context when a diff alone is insufficient.
"""

from __future__ import annotations


# ---- Topic definitions (Step 6 synthesis) -----------------------------------
#
# Each topic produces one section in the final changelog. `domains` lists
# which analysis-by-domain files feed into it. Special tokens:
#   - "localization_mechanics" / "localization_lore" — t/ variants
#   - "extensions" — every extensions/{dlc}/* domain
#   - "*" — every domain

TOPICS: list[dict] = [
    {
        "id": "combat",
        "label": "Combat System",
        "focus": "Shields, weapons, missiles, turrets, AI targeting, weapon heat, disruption mechanics",
        "domains": ["libraries", "aiscripts", "assets/props", "assets/units", "md", "extensions"],
    },
    {
        "id": "new_mechanics",
        "label": "New Game Systems",
        "focus": "New attributes, new gameplay features, new AI behaviors",
        "domains": ["libraries", "aiscripts", "md", "maps", "localization_mechanics", "extensions"],
    },
    {
        "id": "economy_trade",
        "label": "Economy & Trade",
        "focus": (
            "Ware pricing (wares.xml min/avg/max and any inv_* inventory item prices, "
            "including typo/rebalance fixes), production recipes, build costs, "
            "trade AI, resource flow"
        ),
        "domains": ["libraries", "aiscripts", "md", "assets/structures", "extensions"],
    },
    {
        "id": "missions",
        "label": "Mission System",
        "focus": "Mission logic, subscriptions, rewards, faction goals",
        "domains": ["md", "localization_mechanics", "extensions"],
    },
    {
        "id": "story_lore",
        "label": "Story & Lore",
        "focus": "Story dialog, characters, faction lore, encyclopedia entries, tutorial narrative",
        "domains": ["localization_lore", "md", "extensions"],
    },
    {
        "id": "ui",
        "label": "UI & Interface",
        "focus": "Menus, HUD, panels, Lua scripts, notifications",
        "domains": ["ui", "localization_mechanics"],
    },
    {
        "id": "ship_balance",
        "label": "Ship Balance",
        "focus": "Hull, mass, thrust, inertia, drag, crew, storage, engine stats, physics",
        "domains": ["libraries", "assets/units", "assets/props", "extensions"],
    },
    {
        "id": "dlc",
        "label": "DLC-Specific",
        "focus": "Content unique to specific DLCs that doesn't fit other sections",
        "domains": ["extensions"],
    },
    {
        "id": "new_content",
        "label": "New Content",
        "focus": "New ships, wares, stations, story, characters, missions",
        "domains": ["*"],
    },
    {
        "id": "bug_fixes",
        "label": "Bug Fixes",
        "focus": "Corrected values, fixed logic, resolved issues",
        "domains": ["*"],
    },
    {
        "id": "miscellaneous",
        "label": "Miscellaneous",
        "focus": "Anything not covered by other sections",
        "domains": ["*"],
    },
]


# ---- Shared output discipline appended to every markdown-producing prompt ---

_OUTPUT_DISCIPLINE = """\
Output rules (strict):
- Output only the markdown analysis. No preamble, no commentary, no wrapping code fences.
- Never use markdown tables. Use inline bullets: `Item: old -> new | detail | detail`.
- Include specific numeric values (old -> new) for every stat change.
- Only report changes explicitly present in the diff. Never invent, infer, or extrapolate.
- Before finishing, verify every bullet either names a specific item or carries a concrete value change.
"""


def _source_hint(v1: str, v2: str) -> str:
    """Tell the analyzer it can read the local source trees for extra context."""
    return (
        f"Full source trees are available locally at `source/{v1}/` (old) and "
        f"`source/{v2}/` (new). When the diff lacks context to explain a change, "
        f"use your Read/Grep tools on those trees — e.g., read `source/{v2}/libraries/wares.xml` "
        f"for full ware context, or grep `source/{v2}/aiscripts/` for callers of a changed script. "
        f"Only use the local source to enrich explanations of changes present in the diff; "
        f"never introduce changes that aren't in the diff."
    )


# ---- Step 4: analyze prompts -----------------------------------------------


def analyze_general(v1: str, v2: str, label: str, diff_text: str) -> str:
    return f"""You are analyzing an X4 Foundations game diff between version {v1} and {v2}.

Domain: {label}

{_source_hint(v1, v2)}

Classify every change by impact:
- Critical / High Impact: combat balance, economy flow, new mechanics, ship stats, weapon behavior, AI behavior, mission structure
- Medium Impact: specific ship classes, faction-specific changes, tactical, UI functionality, quality-of-life
- Low Impact / Cosmetic: visual effects, sounds, code cleanup, whitespace, internal architecture

Structure the output as three subsections in this exact order:
### {label} — Critical / High Impact
### {label} — Medium Impact
### {label} — Low Impact / Cosmetic

Additional guidance:
- Don't skip small numeric changes — a single number can be a major balance shift.
- Note added files and removed files separately from modified files.
- For XML attribute additions/removals, spell out what was added or removed.

{_OUTPUT_DISCIPLINE}
The diff to analyze follows between the <diff> tags:

<diff>
{diff_text}
</diff>
"""


def analyze_localization_mechanics(v1: str, v2: str, label: str, diff_text: str) -> str:
    return f"""You are analyzing X4 Foundations localization text diffs between version {v1} and {v2}.

Domain: {label}
Focus: MECHANICS-related text only. Ignore story/lore.

{_source_hint(v1, v2)}

Look for:
- New or renamed wares, weapons, equipment, ships, station modules, upgrades
- New or changed UI strings: menu labels, button text, notifications, warnings, settings, tooltips
- Weapon/equipment effect descriptions that imply mechanics changes
- New game features revealed by text (new terms, new concepts)
- Index registration changes for new macros/components (if index diffs are present)

Exclude: story beats, NPC dialog, mission narrative, faction flavor text, encyclopedia lore paragraphs.

Structure as:
### {label} — High Impact
### {label} — Medium Impact
### {label} — Low Impact
### {label} — Index Changes   (include only if index diffs were in the input)

Quote actual text strings for renamed items and new mechanics terms.

{_OUTPUT_DISCIPLINE}
The diff to analyze follows between the <diff> tags. Only English localization (l044) matters; ignore other languages if any slipped through.

<diff>
{diff_text}
</diff>
"""


def analyze_localization_lore(v1: str, v2: str, label: str, diff_text: str) -> str:
    return f"""You are analyzing X4 Foundations localization text diffs between version {v1} and {v2}.

Domain: {label}
Focus: STORY and LORE text only. Ignore mechanics/UI.

{_source_hint(v1, v2)}

Prioritize:
- Unique stations, objects, locations (named entities)
- NPC characters (new, renamed, removed)
- Mission dialog and briefings
- Faction/world lore
- Removed lore
- Tutorial/onboarding narrative
- Genuinely meaningful encyclopedia rewrites (tone shifts, factual changes)

Exclude: mechanics text, UI labels, index registrations, effect descriptions.

Structure as:
### {label} — New Content
### {label} — Rewritten Content
### {label} — Removed Content
### {label} — Minor Fixes

For rewrites, quote both old and new text. For broad tone-only rewrites, summarize the trend in one bullet rather than listing every line.

{_OUTPUT_DISCIPLINE}
The diff to analyze follows between the <diff> tags. English only (l044).

<diff>
{diff_text}
</diff>
"""


# ---- Step 6: topic synthesis ----------------------------------------------


def topic_synthesis(v1: str, v2: str, topic: dict, source_blocks: list[tuple[str, str]]) -> str:
    """Build the synthesis prompt for one topic.

    source_blocks: list of (source_label, source_content). Each is a full
    analysis-by-domain file or a variant thereof.
    """
    sources_rendered = "\n\n".join(
        f"<source name=\"{name}\">\n{content.strip()}\n</source>"
        for name, content in source_blocks
    ) or "<source>(no relevant analysis files)</source>"

    return f"""You are synthesizing a changelog section for X4 Foundations between version {v1} and {v2}.

Theme: {topic['label']}
Focus: {topic['focus']}

You will receive a series of per-domain analysis sources. Pull everything relevant to this theme from them into one cohesive section.

Guidelines:
- Lead with the most impactful changes.
- Aggregate related changes from multiple sources into unified descriptions (e.g., a stat change and its AI-behavior change belong together).
- Note cross-cutting themes that span multiple sources.
- Use ### subsection headers to organize within the theme (e.g., "### Shields", "### Missiles", "### Turret AI").
- If nothing in the sources is relevant, output EXACTLY this one line and nothing else: `No changes.`

{_OUTPUT_DISCIPLINE}
Sources follow:

{sources_rendered}
"""


# ---- Step 7.1: dedup decide (JSON output) ---------------------------------


def dedup_decide(topic_a: str, content_a: str, topic_b: str, content_b: str) -> str:
    return f"""You are comparing two sections of an X4 Foundations changelog and identifying entries that describe the SAME game change. Output JSON only.

Definition of "duplicate":
- Same specific change (same item, same stat, same mechanic) described in both sections, regardless of wording.
- NOT duplicates: related changes, same subsystem, same item class. Only the SAME change.

Instructions:
- For each duplicate, choose which section it thematically fits better. Remove it from the OTHER section.
- Prefer specific topics over catch-all topics. `bug_fixes` and `miscellaneous` are catch-alls; all other topics are specific. When a catch-all and a specific topic both fit, keep the bullet in the specific topic.
- If both sections are equally specific (or both catch-alls), prefer keeping it in section A (listed first).
- Return the exact bullet text (a single line, copied verbatim from the source) that should be deleted. Include the leading `- ` or `* ` marker if present.
- If no duplicates: return empty arrays.

Output format — ONLY this JSON object, no prose, no code fences:
{{"remove_from_a": ["...", "..."], "remove_from_b": ["...", "..."]}}

Section A ({topic_a}):
<section_a>
{content_a.strip()}
</section_a>

Section B ({topic_b}):
<section_b>
{content_b.strip()}
</section_b>
"""


# ---- Mock responses for --mock mode ---------------------------------------

MOCK_ANALYZE = """\
### Mock Domain — Critical / High Impact
- mock.item.stat: 10 -> 20 | example balance change

### Mock Domain — Medium Impact
- mock.feature: new attribute added | tweaked default

### Mock Domain — Low Impact / Cosmetic
- mock.cosmetic: minor tweak
"""

MOCK_TOPIC = """\
### Mock Subsection
- mock.topic.item: 1 -> 2 | representative aggregation across sources
"""

MOCK_DEDUP_JSON = '{"remove_from_a": [], "remove_from_b": []}'
