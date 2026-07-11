"""In-memory :class:`GroupingStore` — the mock-first default for dev/tests.

Same semantics as the SQL store: keyed by ``(user_id, fingerprint)``, first write
wins (a recorded partition is a decision, not a cache to churn).
"""

from __future__ import annotations

from collections.abc import Sequence


class InMemoryGroupingStore:
    """Dict-backed recorded partitions."""

    def __init__(self) -> None:
        self._groupings: dict[tuple[str, str], list[list[str]]] = {}

    def get(self, user_id: str, fingerprint: str) -> list[list[str]] | None:
        stored = self._groupings.get((user_id, fingerprint))
        return [list(group) for group in stored] if stored is not None else None

    def put(self, user_id: str, fingerprint: str, groups: Sequence[Sequence[str]]) -> None:
        self._groupings.setdefault((user_id, fingerprint), [list(group) for group in groups])
