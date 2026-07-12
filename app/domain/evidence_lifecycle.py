"""Evidence lifecycle planning: supersede, never orphan (hardening H6).

When a re-ingest changes a document's chunking — a normalizer bump, a structurer bump,
H5's move to element-derived chunks, or the source itself changing — the rows the fresh
chunk set no longer produces must not stay quietly live (stale text feeding extraction)
and must not be deleted (their ``claim_evidence`` links are provenance). This module
plans the third way, purely:

* every previously-active row absent from the fresh set is **superseded** — marked
  inactive, pointing at its overlapping successor when one is determinable;
* a **human-pinned** superseded row with a determinable successor migrates its pin —
  the retained decision (H1) carries forward to the row that now holds the same text,
  never dies with the stale span; an unmigratable pin becomes a warning, never silence;
* a pin migrates only onto a successor holding **no more than the decided text** (exact
  match, or a piece of it): the human decided THAT text, so a fragment inherits the
  decision soundly — but a successor that merely *contains* the decided text is broader
  than the decision, and stamping it ``human`` would forge a decision over content no
  human reviewed (observed live 2026-07-11: a 9-char word-chunk pin migrated by
  containment onto a 483-char intro paragraph). Broader successors keep the
  supersession pointer; the pin becomes a warning (H7);
* rows whose ref reappears in the fresh set are counted as **reactivated** (the repos
  re-activate on upsert); brand-new refs are counted as **new**;
* a superseded row written under an older normalizer generation produces a
  **mixed-version warning** line — the "old spans superseded on version bump" signal.

Pure and deterministic — no I/O; the service applies the plan through the repository.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.domain.claims import ASSIGNMENT_HUMAN, StoredEvidence


def _normalized(text: str) -> str:
    return " ".join(text.split())


@dataclass(frozen=True)
class Supersession:
    """One row to mark inactive, with its successor when determinable."""

    evidence_id: int
    successor_id: int | None


@dataclass(frozen=True)
class PinMigration:
    """A human pin carried from a superseded row to its successor (H1 preserved)."""

    successor_id: int
    experience_id: int
    superseded_id: int


@dataclass(frozen=True)
class SupersessionPlan:
    """What one document's reconciliation should do, plus its accounting."""

    supersede: tuple[Supersession, ...] = ()
    pin_migrations: tuple[PinMigration, ...] = ()
    new_ids: tuple[int, ...] = ()
    reactivated_ids: tuple[int, ...] = ()
    warnings: tuple[str, ...] = field(default=())


# How a successor relates to the stale text it carries forward. Pin migration keys on
# this: EXACT and PIECE successors hold no more than what the human decided; a BROADER
# successor holds content beyond the decision and must not inherit the pin.
_MATCH_EXACT = "exact"
_MATCH_PIECE = "piece"  # successor text is contained in the stale (decided) text
_MATCH_BROADER = "broader"  # successor text contains the stale text plus more


def _successor_for(
    stale: StoredEvidence, fresh: Sequence[StoredEvidence]
) -> tuple[int, str] | None:
    """The fresh row that carries the stale row's text forward, if determinable.

    Exact normalized-text match first; then containment either way (an old paragraph
    chunk living inside a new element chunk, or a piece of a re-split one). First
    match in document order — deterministic, never a similarity guess. Returns the
    successor id with its match kind.
    """
    stale_text = _normalized(stale.chunk_text)
    if not stale_text:
        return None
    for row in fresh:
        if _normalized(row.chunk_text) == stale_text:
            return row.id, _MATCH_EXACT
    for row in fresh:
        fresh_text = _normalized(row.chunk_text)
        if stale_text in fresh_text:
            return row.id, _MATCH_BROADER
        if fresh_text and fresh_text in stale_text:
            return row.id, _MATCH_PIECE
    return None


def plan_evidence_supersession(
    existing: Sequence[StoredEvidence],
    fresh: Sequence[StoredEvidence],
    *,
    current_normalization_version: int | None = None,
) -> SupersessionPlan:
    """Plan one document's reconciliation: fresh chunk set vs everything persisted.

    ``existing`` is the document's rows as they stood BEFORE this run's upserts
    (active and superseded); ``fresh`` is the rows the run just upserted. After the
    plan is applied, the document's active rows are exactly the fresh set — the H6
    acceptance criterion.
    """
    fresh_ids = {row.id for row in fresh}
    existing_by_id = {row.id: row for row in existing}

    new_ids = tuple(sorted(fresh_ids - existing_by_id.keys()))
    reactivated_ids = tuple(
        sorted(
            row_id
            for row_id, row in existing_by_id.items()
            if row_id in fresh_ids and not row.is_active
        )
    )

    supersessions: list[Supersession] = []
    migrations: list[PinMigration] = []
    warnings: list[str] = []
    migrated_successors: dict[int, int] = {}  # successor id -> pinned experience id
    fresh_by_id = {row.id: row for row in fresh}

    for row in sorted(existing, key=lambda e: e.id):
        if row.id in fresh_ids or not row.is_active:
            continue  # still current, or already-visible history
        match = _successor_for(row, fresh)
        successor_id, match_kind = match if match is not None else (None, None)
        supersessions.append(Supersession(evidence_id=row.id, successor_id=successor_id))
        if (
            current_normalization_version is not None
            and row.normalization_version is not None
            and row.normalization_version != current_normalization_version
        ):
            warnings.append(
                f"evidence {row.id} ({row.source_ref}) written under normalizer "
                f"v{row.normalization_version} superseded under "
                f"v{current_normalization_version}"
            )
        if row.assignment_method != ASSIGNMENT_HUMAN or row.experience_id is None:
            continue
        # A human pin must never die silently with its stale span (H1).
        if successor_id is None:
            warnings.append(
                f"human-pinned evidence {row.id} ({row.source_ref}) superseded with no "
                "determinable successor — re-pin its text manually"
            )
            continue
        if match_kind == _MATCH_BROADER:
            # The successor holds MORE than the decided text: migrating would stamp
            # `human` on content no human reviewed (H7). Pointer stays; pin warns.
            warnings.append(
                f"human-pinned evidence {row.id} ({row.source_ref}) superseded by "
                f"broader evidence {successor_id} — the pin covers only part of the "
                "successor; re-pin manually if the whole successor is that entity's"
            )
            continue
        successor = fresh_by_id[successor_id]
        if successor.assignment_method == ASSIGNMENT_HUMAN:
            continue  # the human already decided the successor; never overwrite (H1)
        prior = migrated_successors.get(successor_id)
        if prior is not None and prior != row.experience_id:
            warnings.append(
                f"conflicting pins converge on evidence {successor_id}: kept experience "
                f"{prior}, dropped {row.experience_id} (from {row.id}) — resolve manually"
            )
            continue
        if prior is None:
            migrated_successors[successor_id] = row.experience_id
            migrations.append(
                PinMigration(
                    successor_id=successor_id,
                    experience_id=row.experience_id,
                    superseded_id=row.id,
                )
            )

    return SupersessionPlan(
        supersede=tuple(supersessions),
        pin_migrations=tuple(migrations),
        new_ids=new_ids,
        reactivated_ids=reactivated_ids,
        warnings=tuple(warnings),
    )


__all__ = [
    "PinMigration",
    "Supersession",
    "SupersessionPlan",
    "plan_evidence_supersession",
]
