"""Per-run memoization for entity_diff.diff_library results.

Module-level dict keyed by the full parameter tuple that shapes the result:
(resolved_old_root, resolved_new_root, file_rel, entity_xpath, key_fn_identity,
include_dlc). Callers pass a str tag for key_fn_identity ('default_id',
'loadouts_rule_composite', ...) or fall back to id(key_fn).

Cache is scoped to a single run. Tests clear between invocations.
"""
from typing import Any, Callable, Hashable


_CACHE: dict[Hashable, Any] = {}


def get_or_compute(key: Hashable, producer: Callable[[], Any]) -> Any:
    if key not in _CACHE:
        _CACHE[key] = producer()
    return _CACHE[key]


def clear() -> None:
    _CACHE.clear()
