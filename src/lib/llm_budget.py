"""Shared LLM-budget helpers used by the chunker and the aggregator.

Single source of truth for the rough-tokens estimator and greedy batch
packing. Both use a 4-chars-per-token approximation; that's accurate
enough for budget-shaping and matches what every model prices at
within ~10% for English+JSON content.
"""
from __future__ import annotations

import json
from typing import Iterable


def est_tokens(payload) -> int:
    """Rough token estimate. Accepts a string (used as-is) or any
    JSON-serializable object (serialized with compact `json.dumps` first).
    """
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, ensure_ascii=False)
    return len(text) // 4


def pack_into_batches(items: Iterable, budget: int, overhead: int
                      ) -> list[list]:
    """Greedy-pack items into batches such that each batch's combined
    token estimate plus prompt overhead stays under `budget`.

    Items are sized via `est_tokens(item)`. An item that already exceeds
    the budget on its own gets its own batch — the caller decides whether
    to retry with a bigger budget or accept that the LLM may reject it.
    """
    batches: list[list] = []
    current: list = []
    current_size = 0
    for item in items:
        item_size = est_tokens(item)
        if current and current_size + item_size + overhead > budget:
            batches.append(current)
            current = []
            current_size = 0
        current.append(item)
        current_size += item_size
    if current:
        batches.append(current)
    return batches
