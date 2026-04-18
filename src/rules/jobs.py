"""Jobs rule: emit outputs for NPC job definition changes.

Single-source under `libraries/jobs.xml` (+ DLC). Each `<job>` is diffed as
a full subtree via an explicit per-child matcher table — there is no
generic recursion fallback. Direct children enumerated as SINGLETON:
`category`, `environment`, `modifiers`, `ship`, `pilot`, `quota`, `orders`,
`startactive`, `location`. Any other direct-child tag → incomplete for that
job with reason `unhandled_child_tag`. Repeated singletons (any of the
enumerated tags appearing more than once on a single `<job>`) → incomplete
with reason `repeated_<tag>`.

The `<job>` element's own `@*` attributes diff fully (no whitelist), and
each singleton child's own attributes diff as `<child_tag>.<attr> old→new`.
Lifecycle signals: add/remove + an explicit `@startactive="false"` row
when the deprecation flag flips.
"""
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.lib.check_incomplete import forward_incomplete, forward_warnings
from src.lib.entity_diff import diff_library
from src.lib.locale import Locale, resolve_attr_ref
from src.lib.rule_output import RuleOutput, render_sources


TAG = 'jobs'
LOCALE_PAGE = 20204

# Direct children of `<job>` each allowed at most 1x per job. Diffed as
# `<child_tag>.<attr> old→new`. Anything else is an incomplete tag.
#
# The spec's initial enumeration covered {category, environment, modifiers,
# ship, pilot, quota, orders, startactive, location} — the bootstrap set
# that carries primary classification + lifecycle. Real X4 jobs.xml ships
# several more direct children, all also singletons per job: basket (cargo
# selection for trader/miner jobs), subordinates (escort spec), encounters
# (AI encounter rates), time (scheduling window), task (fleet task hook),
# masstraffic (spawn density), expirationtime (TTL), and the rare <order>
# stray (dummy-job-only remnant — kept as singleton to stay permissive
# rather than force-contaminate that one job). All verified singletons per
# materialized side via a full 9.00B6 + 8.00H4 sweep.
SINGLETON_CHILDREN = frozenset({
    # Initial spec enumeration.
    'category', 'environment', 'modifiers', 'ship', 'pilot',
    'quota', 'orders', 'startactive', 'location',
    # Real-data extensions (all verified singleton per job).
    'basket', 'subordinates', 'encounters', 'time', 'task',
    'masstraffic', 'expirationtime', 'order',
})

# Classification generic token (stripped from the classification list).
_GENERIC_FILTER = frozenset({'job'})


@dataclass
class _RuleReport:
    """Synthetic DiffReport-shaped wrapper for rule-level diagnostics.

    `diff_library` only emits failures for DLC patch errors / parse errors;
    rule-level assertions (unhandled child tag, repeated singleton) live in
    a parallel bag that rides the same `forward_incomplete` pipeline.
    """
    failures: list[tuple[str, dict]] = field(default_factory=list)
    warnings: list[tuple[str, dict]] = field(default_factory=list)

    @property
    def incomplete(self) -> bool:
        return bool(self.failures)


@dataclass
class _MergedReport:
    """Merge a DiffReport with a _RuleReport for a single `forward_incomplete`
    scope. Rule-level failures already carry the @id `affected_keys`.
    """
    report: object
    rule_report: _RuleReport = field(default_factory=_RuleReport)

    @property
    def incomplete(self) -> bool:
        return bool(getattr(self.report, 'incomplete', False)) or \
               bool(self.rule_report.failures)

    @property
    def failures(self) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        out.extend(list(getattr(self.report, 'failures', []) or []))
        out.extend(self.rule_report.failures)
        return out

    @property
    def warnings(self) -> list[tuple[str, dict]]:
        return list(getattr(self.report, 'warnings', []) or [])


def run(old_root: Path, new_root: Path, changes=None) -> list[RuleOutput]:
    """Emit jobs rule outputs for old_root → new_root.

    `changes` kept for uniform rule interface; unused (library-driven).
    """
    loc_old = Locale.build(old_root)
    loc_new = Locale.build(new_root)
    outputs: list[RuleOutput] = []
    forward_warnings(loc_new.collisions, outputs, tag=TAG)

    report = diff_library(
        old_root, new_root, 'libraries/jobs.xml', './/job',
        key_fn=lambda e: e.get('id'), key_fn_identity='jobs_id',
    )
    rule_report = _RuleReport()

    for rec in report.added:
        outputs.extend(_emit_added(rec, loc_new, rule_report))
    for rec in report.removed:
        outputs.extend(_emit_removed(rec, loc_old, rule_report))
    for rec in report.modified:
        outputs.extend(_emit_modified(rec, loc_old, loc_new, rule_report))

    merged = _MergedReport(report, rule_report)
    forward_incomplete(merged, outputs, tag=TAG)
    forward_warnings(report.warnings, outputs, tag=TAG)
    return outputs


# ---------- classification ----------


def _classify(job: ET.Element) -> list[str]:
    """Return `[<category @faction>, ...<category @tags>, <category @size>]`.

    Nones dropped. `<category>` may be missing (some bootstrap jobs omit it);
    in that case the list is empty. Generic `job` token filtered.
    """
    cat = job.find('category')
    if cat is None:
        return []
    out: list[str] = []
    faction = cat.get('faction')
    if faction:
        out.append(faction)
    tags = (cat.get('tags') or '').strip()
    if tags:
        # `<category @tags>` is a bracketed space-separated token list in X4
        # jobs.xml, e.g. `[fighter,interceptor]` or just `fighter interceptor`.
        for t in _split_tag_list(tags):
            if t:
                out.append(t)
    size = cat.get('size')
    if size:
        out.append(size)
    return [t for t in out if t not in _GENERIC_FILTER]


def _split_tag_list(raw: str) -> list[str]:
    """Split an X4 tag-list attribute into tokens.

    Format variants observed in jobs.xml: `[a,b,c]`, `[a b c]`, or plain
    `a b c`. The brackets are optional; commas and whitespace both separate.
    """
    s = raw.strip()
    if s.startswith('[') and s.endswith(']'):
        s = s[1:-1]
    return [t for t in s.replace(',', ' ').split() if t]


# ---------- child-tag inventory / rule-level diagnostics ----------


def _check_children(job: Optional[ET.Element], jid: str,
                    rule_report: _RuleReport) -> None:
    """Parse-time inventory: enforce strict child enumeration.

    Two rule-level failures possible per job:
    - `unhandled_child_tag` — any direct child tag not in
      SINGLETON_CHILDREN. One failure per distinct unknown tag.
    - `repeated_<tag>` — a singleton tag appearing more than once.
    """
    if job is None:
        return
    counts: dict[str, int] = {}
    for child in job:
        counts[child.tag] = counts.get(child.tag, 0) + 1
    # Unhandled tags.
    unhandled = sorted(t for t in counts if t not in SINGLETON_CHILDREN)
    for tag in unhandled:
        rule_report.failures.append((
            f'job {jid} has unhandled child <{tag}>',
            {
                'reason': 'unhandled_child_tag',
                'job_id': jid,
                'tag': tag,
                'affected_keys': [jid],
            },
        ))
    # Repeated singletons.
    for tag in SINGLETON_CHILDREN:
        if counts.get(tag, 0) > 1:
            rule_report.failures.append((
                f'job {jid} has repeated <{tag}> ({counts[tag]}x)',
                {
                    'reason': f'repeated_{tag}',
                    'job_id': jid,
                    'tag': tag,
                    'count': counts[tag],
                    'affected_keys': [jid],
                },
            ))


# ---------- attribute diffs ----------


def _diff_job_attrs(old_job: ET.Element, new_job: ET.Element) -> list[str]:
    """Diff the `<job>` element's own attributes. No whitelist."""
    out: list[str] = []
    keys = sorted(set(old_job.attrib) | set(new_job.attrib))
    for a in keys:
        ov = old_job.get(a)
        nv = new_job.get(a)
        if ov != nv:
            out.append(f'{a} {ov}→{nv}')
    return out


def _diff_singleton_child(old_job: ET.Element, new_job: ET.Element,
                          tag: str) -> list[str]:
    """Diff one singleton child's attributes as `<tag>.<attr> old→new`.

    If the child exists on one side only → `<tag> added` or `<tag> removed`.
    """
    old_el = old_job.find(tag)
    new_el = new_job.find(tag)
    if old_el is None and new_el is None:
        return []
    if old_el is None:
        return [f'{tag} added']
    if new_el is None:
        return [f'{tag} removed']
    out: list[str] = []
    keys = sorted(set(old_el.attrib) | set(new_el.attrib))
    for a in keys:
        ov = old_el.get(a)
        nv = new_el.get(a)
        if ov != nv:
            out.append(f'{tag}.{a} {ov}→{nv}')
    return out


# ---------- emitters ----------


def _format(name: str, classifications: list[str], sources_label: str,
            parts: list[str]) -> str:
    cls = f' ({", ".join(classifications)})' if classifications else ''
    src = f' {sources_label}' if sources_label else ''
    return f'[{TAG}] {name}{cls}{src}: {", ".join(parts)}'


def _display(job: ET.Element, loc: Locale, jid: str) -> str:
    """`@name` → locale lookup via `resolve_attr_ref`; fallback `@id`."""
    return resolve_attr_ref(job, loc, attr='name', fallback=jid)


def _emit_added(rec, loc_new: Locale,
                rule_report: _RuleReport) -> list[RuleOutput]:
    job = rec.element
    jid = rec.key
    _check_children(job, jid, rule_report)
    name = _display(job, loc_new, jid)
    classifications = _classify(job)
    sources_label = render_sources(None, rec.sources)
    parts = ['NEW']
    if job.get('startactive') == 'false':
        parts.append('startactive=false')
    text = _format(name, classifications, sources_label, parts)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': jid,
        'kind': 'added',
        'classifications': classifications,
        'job_id': jid,
        'new_sources': list(rec.sources),
        'sources': list(rec.sources),
        'source': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_removed(rec, loc_old: Locale,
                  rule_report: _RuleReport) -> list[RuleOutput]:
    job = rec.element
    jid = rec.key
    _check_children(job, jid, rule_report)
    name = _display(job, loc_old, jid)
    classifications = _classify(job)
    sources_label = render_sources(rec.sources, None)
    text = _format(name, classifications, sources_label, ['REMOVED'])
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': jid,
        'kind': 'removed',
        'classifications': classifications,
        'job_id': jid,
        'old_sources': list(rec.sources),
        'sources': list(rec.sources),
        'source': list(rec.sources),
        'ref_sources': dict(rec.ref_sources),
    })]


def _emit_modified(rec, loc_old: Locale, loc_new: Locale,
                   rule_report: _RuleReport) -> list[RuleOutput]:
    jid = rec.key
    pre_fail_count = len(rule_report.failures)
    _check_children(rec.old, jid, rule_report)
    _check_children(rec.new, jid, rule_report)
    structural_issue = len(rule_report.failures) > pre_fail_count

    name = _display(rec.new, loc_new, jid)
    if name == jid:
        name = _display(rec.old, loc_old, jid)
    classifications = _classify(rec.new)

    changes: list[str] = []
    # Job-level attrs (no whitelist).
    changes.extend(_diff_job_attrs(rec.old, rec.new))
    # Each singleton child's attrs.
    for tag in sorted(SINGLETON_CHILDREN):
        changes.extend(_diff_singleton_child(rec.old, rec.new, tag))

    # Explicit startactive=false lifecycle row — prepended.
    old_sa = rec.old.get('startactive')
    new_sa = rec.new.get('startactive')
    if new_sa == 'false' and old_sa != 'false':
        # The attr-level diff already surfaces this as `startactive None→false`.
        # Prepend the named lifecycle token so downstream consumers can read it
        # without parsing the attr diff format.
        changes.insert(0, 'DEPRECATED (startactive=false)')
    elif old_sa == 'false' and new_sa != 'false':
        changes.insert(0, 'un-deprecated (startactive cleared)')

    # A job that was structurally changed (e.g. repeated singleton, unhandled
    # child) but whose singleton-based diff found nothing still needs a row so
    # `forward_incomplete` can mark it — otherwise the aggregate sentinel is
    # the only signal and the per-entity extras.incomplete=True attribution
    # disappears.
    if not changes and not structural_issue:
        return []
    if not changes and structural_issue:
        changes = ['structural change (see incomplete diagnostic)']

    sources_label = render_sources(rec.old_sources, rec.new_sources)
    text = _format(name, classifications, sources_label, changes)
    return [RuleOutput(tag=TAG, text=text, extras={
        'entity_key': jid,
        'kind': 'modified',
        'classifications': classifications,
        'job_id': jid,
        'old_source_files': list(rec.old_source_files),
        'new_source_files': list(rec.new_source_files),
        'old_sources': list(rec.old_sources),
        'new_sources': list(rec.new_sources),
        'source': list(rec.new_sources),
        'ref_sources': dict(rec.new_ref_sources),
    })]
