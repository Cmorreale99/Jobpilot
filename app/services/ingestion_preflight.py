"""Ingestion preflight service: manifest + candidate reconciliation over cached raw.

The I/O half of :mod:`app.domain.ingestion_preflight`. Everything here is **zero-cost
and side-effect-free on canonical state**: sources are enumerated from the H2 raw
capture layer (no network, no refetch), the candidate structure derivation runs the
current structurer **in memory** over cached raw text (the canonical element tables
are never written), and the only writes are manifest/ledger JSON files under
``ARTIFACTS_DIR``. No module in this path imports the LLM layer — a paid model call
is unreachable by construction.

Checkpointing: the ledger file doubles as the resume state. Each object's row is
keyed by ``(source_id, content_hash, structurer_version)`` — re-running skips every
object whose key already has a row, so completed work is never repeated and an
interrupted run resumes at the first incomplete artifact.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.domain.claims import SOURCE_GITHUB_COMMIT, ClaimRepository, ExperienceStatus
from app.domain.ingestion_preflight import (
    LedgerRow,
    ManifestEntry,
    ProjectCandidate,
    ReconciliationReport,
    SourceManifest,
    build_manifest,
    count_duplicates,
    reconcile_object,
)
from app.domain.source_capture import CapturedSourceVersion, SourceCaptureStore
from app.domain.source_structure import (
    STRUCTURER_VERSION,
    structure_commit_message,
    structure_source_text,
)

logger = logging.getLogger(__name__)

# The non-negotiable discovery acceptance: this project must be explicitly present in
# the preflight manifest or ingestion must not run.
REQUIRED_PROJECTS = ("paper recommender system",)


def _universe(
    user_id: str, capture_store: SourceCaptureStore
) -> list[tuple[object, CapturedSourceVersion | None]]:
    return [
        (
            document,
            capture_store.get_active_version(user_id, document.source_type, document.source_ref),
        )
        for document in capture_store.list_documents(user_id)
    ]


def build_source_manifest(
    user_id: str,
    capture_store: SourceCaptureStore,
    repository: ClaimRepository,
    *,
    required_projects: tuple[str, ...] = REQUIRED_PROJECTS,
) -> SourceManifest:
    """The authoritative manifest: every captured source, scanned for known projects."""
    projects = [
        ProjectCandidate(name=e.name, aliases=tuple(e.aliases))
        for e in repository.list_experiences(user_id)
        if e.status in (ExperienceStatus.CONFIRMED, ExperienceStatus.PROPOSED)
    ]
    return build_manifest(
        _universe(user_id, capture_store),  # type: ignore[arg-type]
        projects,
        required_projects=required_projects,
    )


def format_preflight_summary(manifest: SourceManifest) -> str:
    """The human preflight report, ending with the explicit acceptance line(s)."""
    lines = [
        "== INGESTION PREFLIGHT (zero-cost, cached raw only) ==",
        f"total document count:        {manifest.document_count}",
        f"total GitHub repositories:   {len(manifest.repositories)}",
        f"total repository files:      {manifest.repository_file_count} "
        "(READMEs + policy-window commits — the configured repo universe)",
        "repositories:",
        *[f"  - {repo}" for repo in manifest.repositories],
        "discovered project/repository names:",
        *[f"  - {name}" for name in manifest.discovered_project_names],
    ]
    for name, present in manifest.required_projects_present.items():
        flag = name.upper().replace(" ", "_")
        lines.append(f"{flag}_PRESENT={'true' if present else 'false'}")
    return "\n".join(lines)


def required_projects_present(manifest: SourceManifest) -> bool:
    return all(manifest.required_projects_present.values())


# --- candidate reconciliation (Phases 3-4) ---------------------------------------------


def _row_key(entry: ManifestEntry) -> str:
    return f"{entry.source_id}:{entry.content_hash}:{STRUCTURER_VERSION}"


@dataclass(frozen=True)
class CandidateRun:
    """One reconciliation pass: the report plus how much work was actually done."""

    report: ReconciliationReport
    recomputed: int
    reused: int


def run_candidate_reconciliation(
    user_id: str,
    capture_store: SourceCaptureStore,
    manifest: SourceManifest,
    *,
    checkpoint_path: Path,
) -> CandidateRun:
    """Derive candidate structure for every manifested object and reconcile it.

    Cached raw inputs only — nothing is fetched. The derivation is in-memory; the
    canonical element tables are untouched (an isolated candidate generation). Rows
    already checkpointed for the same ``(source, content_hash, structurer_version)``
    are reused verbatim: interruption resumes at the first incomplete artifact and
    completed work is never repeated.
    """
    checkpoint: dict[str, dict[str, object]] = {}
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8")).get("rows", {})

    rows: list[LedgerRow] = []
    recomputed = 0
    reused = 0
    known_version_ids: set[int] = set()
    for entry in manifest.entries:
        version = capture_store.get_active_version(user_id, entry.system, entry.path)
        if version is not None:
            known_version_ids.add(version.id)
        key = _row_key(entry)
        cached = checkpoint.get(key)
        if cached is not None and version is not None:
            rows.append(LedgerRow(**cached))  # type: ignore[arg-type]
            reused += 1
            continue
        elements = []
        if version is not None:
            elements = (
                structure_commit_message(version.raw_text)
                if entry.system == SOURCE_GITHUB_COMMIT
                else structure_source_text(version.raw_text)
            )
        row = reconcile_object(entry, version, elements)
        rows.append(row)
        recomputed += 1
        checkpoint[key] = row.to_dict()
        # Checkpoint after EVERY object: an interruption loses at most one artifact.
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(
            json.dumps({"structurer_version": STRUCTURER_VERSION, "rows": checkpoint}, indent=1),
            encoding="utf-8",
        )

    report = ReconciliationReport(
        rows=tuple(rows),
        orphaned_elements=0,  # candidate derivation persists nothing, so it can orphan nothing
        unmanifested_outputs=0,  # every derived tree originated from a manifest entry
        duplicate_objects=count_duplicates(manifest.entries),
    )
    logger.info(
        "candidate reconciliation for %s: %d object(s), %d recomputed, %d reused, %s",
        user_id,
        len(rows),
        recomputed,
        reused,
        "PASS" if report.passed else "FAIL",
    )
    return CandidateRun(report=report, recomputed=recomputed, reused=reused)


def format_reconciliation_summary(run: CandidateRun) -> str:
    metrics = run.report.metrics
    lines = ["== RAW-TO-EXTRACTED RECONCILIATION (candidate generation) =="]
    lines.extend(f"{name}: {value}" for name, value in metrics.items())
    lines.append(f"objects recomputed: {run.recomputed}; reused from checkpoint: {run.reused}")
    failing = [row for row in run.report.rows if row.validation_status != "ok"]
    for row in failing[:20]:
        lines.append(f"  FAIL {row.system} {row.path}: {'; '.join(row.failures)}")
    if len(failing) > 20:
        lines.append(f"  ... and {len(failing) - 20} more")
    lines.append(f"generation: {'PASS' if run.report.passed else 'FAIL'}")
    return "\n".join(lines)


__all__ = [
    "REQUIRED_PROJECTS",
    "CandidateRun",
    "build_source_manifest",
    "format_preflight_summary",
    "format_reconciliation_summary",
    "required_projects_present",
    "run_candidate_reconciliation",
]
