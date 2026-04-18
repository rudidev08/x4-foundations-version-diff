"""Forwarding helpers: rule authors funnel DiffReport failures/warnings through
these helpers so the 'no silent changes' contract is enforced uniformly.

- assert_complete(outputs): raises IncompleteRunError if any output is incomplete.
- forward_incomplete(report, outputs, tag, subsource=None): mutates outputs to
  mark contaminated rows + appends sentinel.
- forward_incomplete_many(pairs, outputs, tag): multi-sub-source version; each
  (report, subsource_label) pair scopes the contamination check.
- forward_warnings(warnings, outputs, tag): appends one warning-kind output per
  warning tuple.

All mutations are in-place; rules return outputs unchanged after calling.
"""
from typing import Iterable, Optional

from src.lib.rule_output import RuleOutput, diagnostic_entity_key


class IncompleteRunError(RuntimeError):
    pass


def assert_complete(outputs: Iterable[RuleOutput]) -> None:
    bad = [o for o in outputs if o.extras.get('incomplete')]
    if bad:
        reasons = [o.text for o in bad]
        raise IncompleteRunError('\n'.join(reasons))


def forward_incomplete(report, outputs: list[RuleOutput], tag: str,
                       subsource: Optional[str] = None) -> None:
    if not getattr(report, 'incomplete', False):
        return
    affected_all: set = set()
    global_contamination = False
    for _, extras in report.failures:
        ak = extras.get('affected_keys') or []
        if not ak:
            global_contamination = True
        else:
            affected_all.update(ak)
    for out in outputs:
        if subsource is not None and out.extras.get('subsource') != subsource:
            continue
        ek = out.extras.get('entity_key')
        if global_contamination or ek in affected_all:
            out.extras['incomplete'] = True
    text = f'[{tag}] RULE INCOMPLETE: {len(report.failures)} patch failures'
    if subsource is not None:
        text += f' ({subsource})'
    outputs.append(RuleOutput(tag=tag, text=text, extras={
        'entity_key': diagnostic_entity_key(tag, text),
        'kind': 'incomplete',
        'subsource': 'diagnostic' if subsource is None else subsource,
        'classifications': [],
        'incomplete': True,
        'failures': report.failures,
    }))


def forward_incomplete_many(pairs: Iterable[tuple], outputs: list[RuleOutput],
                            tag: str) -> None:
    for report, subsource_label in pairs:
        forward_incomplete(report, outputs, tag=tag, subsource=subsource_label)


def forward_warnings(warnings: Iterable[tuple[str, dict]],
                     outputs: list[RuleOutput], tag: str) -> None:
    for text, extras in warnings:
        outputs.append(RuleOutput(tag=tag, text=f'[{tag}] WARNING: {text}', extras={
            'entity_key': diagnostic_entity_key(tag, text),
            'kind': 'warning',
            'subsource': 'diagnostic',
            'classifications': [],
            'warning': True,
            'details': extras,
        }))
