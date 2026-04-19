"""Reviewed failures/warnings from real-data runs.

Every entry justifies WHY this incomplete/warning is known and acceptable.
Unreviewed items block production emission (spec: 'no silent changes').

Format: list of dicts with:
- tag: rule tag (e.g., 'stations')
- entity_key: str or tuple
- reason: short reason code from DiffReport.failures or .warnings
- justification: english sentence
- seen_in_pairs: list of (old_ver, new_ver) tuples
"""

ALLOWLIST = [
    {
        'tag': 'wares',
        'entity_key': ('diagnostic', 'wares', '6d353fc406a6'),
        'reason': 'if_raw_dependency',
        'justification': (
            'X4 9.00 ships mini_01/mini_02/pirate/split DLC wares.xml files with '
            'overlapping if= gates on //wares/production/method[@id=\'closedloop\'] '
            'that the cross-DLC classifier flags as read-after-write. Failures do '
            'not target Wave 1 ware ids (all affected_keys are method ids like '
            '"closedloop"), so no ware row is contaminated; sentinel is informational.'
        ),
        'seen_in_pairs': [('8.00H4', '9.00B6')],
    },
    {
        'tag': 'turrets',
        'entity_key': ('diagnostic', 'turrets', '41a7471a51e6'),
        'reason': 'if_raw_dependency',
        'justification': (
            'Same wares.xml cross-DLC if= gate conflicts as the wares rule entry '
            'above. affected_keys are method/engine/weapon ids, never turret ware '
            'ids; no turret row is contaminated. Sentinel is informational; the '
            'rule output for every turret ware remains complete.'
        ),
        'seen_in_pairs': [('8.00H4', '9.00B6')],
    },
    {
        'tag': 'ships',
        'entity_key': ('diagnostic', 'ships', 'c7f01003212b'),
        'reason': 'parse_error',
        'justification': (
            'X4 8.00H4 ships two empty ship macro files: '
            'assets/units/size_m/macros/ship_gen_m_transdrone_container_02_a_macro.xml '
            'and its size_s sibling (both 0 bytes on disk). The rule emits a '
            'parse-error sentinel for the macro sub-source as designed; affected_keys '
            'is empty so the contamination scope stays within subsource=macro and no '
            'real ship row is tainted. The empty files disappear in 9.00B6 (replaced '
            'by ego_dlc_terran). Informational only.'
        ),
        'seen_in_pairs': [('8.00H4', '9.00B6')],
    },
    {
        'tag': 'ships',
        'entity_key': ('diagnostic', 'ships', '178ce5bb695e'),
        'reason': 'if_raw_dependency',
        'justification': (
            'Same wares.xml cross-DLC if= gate conflicts as the wares rule entry '
            'above. affected_keys are method ids (e.g., "closedloop"), never ship '
            'ware ids; no ship ware row is contaminated. Sentinel is informational; '
            'all ship/drone ware outputs remain complete.'
        ),
        'seen_in_pairs': [('8.00H4', '9.00B6')],
    },
    {
        'tag': 'ships',
        'entity_key': ('diagnostic', 'ships', 'a460bc51486c'),
        'reason': 'add_target_missing',
        'justification': (
            'X4 9.00 ships.xml DLC patches reference ship ids that do not exist in '
            'core (split_scout_s, terran_scout_s, scavenger_smuggler_container_s). '
            'add_target_missing failures are scoped to those specific ids via '
            'affected_keys; none of them have ever shipped in core ships.xml, so no '
            'existing role row is contaminated. Sentinel is informational.'
        ),
        'seen_in_pairs': [('8.00H4', '9.00B6')],
    },
    {
        'tag': 'factions',
        'entity_key': ('diagnostic', 'factions', '7032366dfbaf'),
        'reason': 'add_id_collision',
        'justification': (
            'X4 9.00 factions.xml sees both ego_dlc_terran and ego_dlc_timelines '
            'attempt to add a <faction id="terran">, and timelines gates subsequent '
            'ops on not(//faction[@id="terran"]) — the cross-DLC classifier flags '
            'both as add_id_collision + if_raw_gate_flip (38 gate flips, 2 id '
            'collisions). Separately, several DLCs <add> relations to faction ids '
            'that do not exist in core factions.xml (court, fallensplit, freesplit, '
            'loanshark, pioneers, scavenger, split, terran, yaki — 32 add_target_'
            'missing failures). All 72 failures are scoped to specific faction ids '
            'via affected_keys; zero real faction rows changed between 8.00H4 and '
            '9.00B6 so no row is contaminated. Sentinel is informational.'
        ),
        'seen_in_pairs': [('8.00H4', '9.00B6')],
    },
    {
        'tag': 'jobs',
        'entity_key': ('diagnostic', 'jobs', '328ffb34df6d'),
        'reason': 'add_target_missing',
        'justification': (
            'X4 9.00 jobs.xml DLC patches attempt to <add> into four job ids that '
            'do not exist in core (loanshark_plunderer_iceunion, zyarth_scout_'
            'patrol_s, terran_scout_patrol_s, scavenger_free_tug_m — 8 patch ops '
            'across mini_02 / pirate / split DLCs). Three of the four ids never '
            'shipped in core, so no core job row is contaminated. The fourth '
            '(scavenger_free_tug_m) is a real modified job; its row is flagged '
            'separately. Sentinel is informational.'
        ),
        'seen_in_pairs': [('8.00H4', '9.00B6')],
    },
    {
        'tag': 'jobs',
        'entity_key': 'scavenger_free_tug_m',
        'reason': None,
        'justification': (
            'X4 9.00 pirate DLC adds two <basket> children to scavenger_free_tug_m '
            'via <add sel="/jobs/job[@id=\'scavenger_free_tug_m\']">; core job '
            'already has a <basket> child, so the DLC patches end up synthesizing '
            '<add sel=...> targeting THAT job (which exists). Unrelated to the '
            'diagnostic sentinel\'s 8 patch-miss entries on other ids, but the '
            'scavenger_free_tug_m row ends up tagged incomplete via '
            'affected_keys=[\'scavenger_free_tug_m\'] because a separate '
            'add_target_missing reuses that id. The modified row still surfaces '
            'real attribute-level diffs; manually reviewed 9.00B6 and the '
            'basket/subordinates content is legitimate. Informational.'
        ),
        'seen_in_pairs': [('8.00H4', '9.00B6')],
    },
    {
        'tag': 'gamelogic',
        'entity_key': ('diagnostic', 'gamelogic', 'ddab569d4cbd'),
        'reason': 'unsupported_xpath',
        'justification': (
            'X4 9.00 pirate DLC aiscript extensions/ego_dlc_pirate/aiscripts/'
            'order.plunder.xml has two <add> ops whose selectors contain '
            'nested quotes (`//create_order[@id="&apos;Attack&apos;"]`). The '
            'current XPath subset evaluator does not support predicate '
            'values with nested single quotes inside double-quoted predicate '
            'strings, so both ops fail with reason unsupported_xpath. The '
            'failures are scoped to the order.plunder aiscript via '
            'affected_keys, contaminating only that one row. Supporting '
            'nested quotes in the XPath parser is a separate enhancement '
            'tracked outside this rule. Sentinel is informational.'
        ),
        'seen_in_pairs': [('8.00H4', '9.00B6')],
    },
    {
        'tag': 'gamelogic',
        'entity_key': ('aiscript', 'order.plunder'),
        'reason': None,
        'justification': (
            'The order.plunder aiscript row is marked incomplete because '
            'two pirate DLC <add> ops against it failed with unsupported_'
            'xpath (see gamelogic diagnostic sentinel above). The '
            'effective-script diff still surfaces the real pre-patch delta '
            'between 8.00H4 and 9.00B6 core order.plunder.xml content — '
            'only the DLC-patch-local changes near the //create_order[@id='
            '"\'Attack\'"] anchor are missing. Manually reviewed the 9.00B6 '
            'file and the unpatched diff content is legitimate core-only '
            'change; when the XPath parser gains nested-quote support this '
            'allowlist entry can be retired.'
        ),
        'seen_in_pairs': [('8.00H4', '9.00B6')],
    },
    # Helper-harness entries for the test_realdata_helpers.AllowlistRespectedTest
    # check. Helpers run diff_library across 7 library files; same underlying
    # cross-DLC patterns as the per-rule diagnostics surface under helper_<name>
    # tags. Pair-scoped to 8.00H4→9.00B6 canonical pair.
    {
        'tag': 'helper_wares',
        'entity_key': None,
        'reason': 'if_raw_dependency',
        'justification': (
            'Same closedloop if= cross-DLC conflicts as the wares-rule '
            'diagnostic sentinel. 880 failures on the canonical pair; '
            'affected_keys are always method ids (e.g. "closedloop"), never '
            'real ware ids.'
        ),
        'seen_in_pairs': [('8.00H4', '9.00B6')],
    },
    {
        'tag': 'helper_wares',
        'entity_key': None,
        'reason': 'if_raw_gate_flip',
        'justification': (
            'Timelines DLC has `if="not(//wares/production/method[@id=\'terran\'])"` '
            'gates that flip once terran DLC ops apply. 80 failures; all scoped '
            'to specific production method ids via affected_keys.'
        ),
        'seen_in_pairs': [('8.00H4', '9.00B6')],
    },
    {
        'tag': 'helper_wares',
        'entity_key': None,
        'reason': 'add_target_missing',
        'justification': (
            'DLC wares.xml patches reference missile ware ids that do not '
            'exist in core (missile_disruptor_light_mk1, missile_gen_s_*, '
            'missile_scatter_heavy_mk1 etc). 10 failures scoped via '
            'affected_keys to those specific ids; none ever shipped in core.'
        ),
        'seen_in_pairs': [('8.00H4', '9.00B6')],
    },
    {
        'tag': 'helper_jobs',
        'entity_key': None,
        'reason': 'add_target_missing',
        'justification': (
            'Same pattern as the jobs-rule diagnostic: DLC jobs.xml patches '
            '<add> into 4 job ids (loanshark_plunderer_iceunion, zyarth_scout_'
            'patrol_s, terran_scout_patrol_s, scavenger_free_tug_m) across '
            'mini_02 / pirate / split DLCs. 8 failures.'
        ),
        'seen_in_pairs': [('8.00H4', '9.00B6')],
    },
    {
        'tag': 'gamestarts',
        'entity_key': ('diagnostic', 'gamestarts', '5db4b6a823be'),
        'reason': 'write_write_conflict',
        'justification': (
            'X4 9.00 gamestarts.xml has boron+split+terran DLCs all replacing the '
            'same x4ep1_gamestart_tutorial1 and x4ep1_gamestart_tutorial2 '
            'descendant attributes (@name, @description, player/@macro, info '
            'item values) — canonical write-write contention. Split/boron also '
            'collide on subtree invalidation for x4ep1_gamestart_split1/split2 '
            '(split replaces the whole gamestart, boron adds a '
            'universe/factions/relations child beneath it). The 38 failures are '
            'scoped via affected_keys to those four ids, none of which surface '
            'as modified rows in the effective 8.00H4→9.00B6 diff (the DLC '
            'overlays match on both sides, cancelling to no net change on the '
            'core tutorial/split entries). No real gamestart row is '
            'contaminated; sentinel is informational.'
        ),
        'seen_in_pairs': [('8.00H4', '9.00B6')],
    },
]


def is_allowlisted(output) -> bool:
    """True iff output matches an allowlist entry."""
    extras = output.extras
    tag = getattr(output, 'tag', None)
    key = extras.get('entity_key')
    reason = (extras.get('failures') or [None])[0]
    reason_code = None
    if isinstance(reason, tuple) and len(reason) == 2:
        reason_code = reason[1].get('reason') if isinstance(reason[1], dict) else None
    for entry in ALLOWLIST:
        if entry.get('tag') == tag and entry.get('entity_key') == key:
            if entry.get('reason') is None or entry.get('reason') == reason_code:
                return True
    return False
