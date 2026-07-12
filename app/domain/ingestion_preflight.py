"""Zero-cost ingestion preflight: the authoritative source manifest + reconciliation.

Pure and deterministic — no I/O, no network, no LLM. The service/tool layer hands this
module the captured documents (H2 raw layer), their active versions, their persisted
elements, and the confirmed roster; it computes:

* the **source manifest** — one entry per discovered source object with identity,
  provenance, hashes, and the deterministic *project references* found in its raw
  text (normalized substring scan against roster names/aliases and any explicitly
  required project names). Discovery that cannot name a known project in the sources
  is a discovery failure, surfaced as ``required_projects_present``.
* the **reconciliation ledger** — per manifested object: discovered / raw-captured /
  hash-verified / extraction-complete / structure-complete (+ SourceElement count) /
  provenance-complete / output hash / validation status, with corpus coverage
  computed deterministically from counts (never an LLM score).

A generation passes only at 100.000% on every coverage metric with zero orphans,
zero unmanifested outputs, and zero duplicates.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.domain.source_capture import (
    INGESTION_FAILED,
    CapturedSourceDocument,
    CapturedSourceVersion,
    StoredSourceElement,
    raw_content_hash,
)
from app.domain.source_structure import SourceElement, verify_full_coverage

_WS_RE = re.compile(r"\s+")
_IDENT_SEP_RE = re.compile(r"[-_/.]+")


def normalize_for_match(text: str) -> str:
    """Casefolded, whitespace-collapsed, identifier-separator-tolerant form.

    ``paper-recommender_system`` and ``Paper recommender system`` normalize alike, so
    a project is discoverable by repository name, directory name, title, or prose.
    """
    return _WS_RE.sub(" ", _IDENT_SEP_RE.sub(" ", text)).casefold().strip()


@dataclass(frozen=True)
class ProjectCandidate:
    """One known real-world project to search the sources for."""

    name: str
    aliases: tuple[str, ...] = ()

    def match_terms(self) -> tuple[str, ...]:
        terms = [normalize_for_match(self.name)]
        for alias in self.aliases:
            normalized = normalize_for_match(alias)
            if normalized and normalized not in terms:
                terms.append(normalized)
            # A repo alias like ``owner/repo`` should also match by bare repo name.
            tail = normalized.rsplit(" ", 1)[-1] if " " in normalized else None
            if tail and len(tail) >= 4 and tail not in terms:
                terms.append(tail)
        return tuple(term for term in terms if term)


@dataclass(frozen=True)
class ManifestEntry:
    """One source object in the authoritative manifest."""

    source_id: int
    name: str
    path: str
    system: str
    mime_type: str | None
    size_bytes: int | None
    content_hash: str | None
    repository: str | None
    commit_sha: str | None
    retrieved_at: str | None
    raw_cache_location: str | None
    extraction_status: str
    project_references: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "name": self.name,
            "path": self.path,
            "system": self.system,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "content_hash": self.content_hash,
            "repository": self.repository,
            "commit_sha": self.commit_sha,
            "retrieved_at": self.retrieved_at,
            "raw_cache_location": self.raw_cache_location,
            "extraction_status": self.extraction_status,
            "project_references": list(self.project_references),
        }


@dataclass(frozen=True)
class SourceManifest:
    """The authoritative preflight manifest for one user's source universe."""

    entries: tuple[ManifestEntry, ...]
    projects_searched: tuple[str, ...]
    required_projects_present: Mapping[str, bool]

    @property
    def document_count(self) -> int:
        return len(self.entries)

    @property
    def repositories(self) -> tuple[str, ...]:
        seen: list[str] = []
        for entry in self.entries:
            if entry.repository and entry.repository not in seen:
                seen.append(entry.repository)
        return tuple(seen)

    @property
    def repository_file_count(self) -> int:
        return sum(1 for entry in self.entries if entry.repository)

    @property
    def discovered_project_names(self) -> tuple[str, ...]:
        seen: list[str] = []
        for entry in self.entries:
            for reference in entry.project_references:
                if reference not in seen:
                    seen.append(reference)
        return tuple(seen)

    def to_dict(self) -> dict[str, object]:
        return {
            "documents": self.document_count,
            "repositories": list(self.repositories),
            "repository_file_count": self.repository_file_count,
            "projects_searched": list(self.projects_searched),
            "required_projects_present": dict(self.required_projects_present),
            "discovered_project_names": list(self.discovered_project_names),
            "entries": [entry.to_dict() for entry in self.entries],
        }


def _split_repo_ref(source_type: str, source_ref: str) -> tuple[str | None, str | None]:
    if not source_type.startswith("github"):
        return None, None
    if "@" in source_ref:
        repo, _, sha = source_ref.partition("@")
        return repo, sha
    return source_ref, None


def build_manifest(
    documents: Sequence[tuple[CapturedSourceDocument, CapturedSourceVersion | None]],
    projects: Sequence[ProjectCandidate],
    *,
    required_projects: Sequence[str] = (),
) -> SourceManifest:
    """Assemble the manifest from the raw-capture layer — discovery is a text fact.

    Each source's raw text, title, and path are scanned (normalized substring match)
    for every known project's name and aliases. ``required_projects`` are additionally
    searched verbatim even when absent from the roster, so a missing roster entry can
    never mask a discovery failure.
    """
    candidates = list(projects)
    known = {normalize_for_match(p.name) for p in projects}
    for name in required_projects:
        if normalize_for_match(name) not in known:
            candidates.append(ProjectCandidate(name=name))

    entries: list[ManifestEntry] = []
    for document, version in documents:
        haystacks = [normalize_for_match(document.title or "")]
        haystacks.append(normalize_for_match(document.source_ref))
        if version is not None:
            haystacks.append(normalize_for_match(version.raw_text))
        references: list[str] = []
        for candidate in candidates:
            if any(term in haystack for term in candidate.match_terms() for haystack in haystacks):
                references.append(candidate.name)
        repository, sha = _split_repo_ref(document.source_type, document.source_ref)
        entries.append(
            ManifestEntry(
                source_id=document.id,
                name=document.title or document.source_ref,
                path=document.source_ref,
                system=document.source_type,
                mime_type=document.mime_type,
                size_bytes=(
                    document.size_bytes
                    if document.size_bytes is not None
                    else (len(version.raw_text.encode("utf-8")) if version is not None else None)
                ),
                content_hash=version.content_hash if version is not None else None,
                repository=repository,
                commit_sha=sha,
                retrieved_at=(
                    version.fetched_at.isoformat()
                    if version is not None and version.fetched_at is not None
                    else None
                ),
                raw_cache_location=(
                    f"source_document_versions.id={version.id}" if version is not None else None
                ),
                extraction_status=(
                    (version.ingestion_status or "captured")
                    if version is not None
                    else "uncaptured"
                ),
                project_references=tuple(references),
            )
        )

    referenced = {
        normalize_for_match(name) for entry in entries for name in entry.project_references
    }
    required_present = {
        name: (normalize_for_match(name) in referenced) for name in required_projects
    }
    return SourceManifest(
        entries=tuple(entries),
        projects_searched=tuple(candidate.name for candidate in candidates),
        required_projects_present=required_present,
    )


# --- reconciliation ledger (Phase 4) --------------------------------------------------

LEDGER_OK = "ok"
LEDGER_FAILED = "failed"


@dataclass(frozen=True)
class LedgerRow:
    """The deterministic per-object reconciliation record."""

    source_id: int
    path: str
    system: str
    discovered: bool
    raw_captured: bool
    hash_verified: bool
    extraction_complete: bool
    structure_complete: bool
    element_count: int
    provenance_complete: bool
    output_hash: str | None
    validation_status: str
    failures: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "path": self.path,
            "system": self.system,
            "discovered": self.discovered,
            "raw_captured": self.raw_captured,
            "hash_verified": self.hash_verified,
            "extraction_complete": self.extraction_complete,
            "structure_complete": self.structure_complete,
            "element_count": self.element_count,
            "provenance_complete": self.provenance_complete,
            "output_hash": self.output_hash,
            "validation_status": self.validation_status,
            "failures": list(self.failures),
        }


@dataclass(frozen=True)
class ReconciliationReport:
    """Corpus verdict: coverage percentages computed from counts, nothing else."""

    rows: tuple[LedgerRow, ...]
    orphaned_elements: int
    unmanifested_outputs: int
    duplicate_objects: int
    llm_calls: int = 0  # by construction: this layer can make none

    def _pct(self, predicate: str) -> float:
        if not self.rows:
            return 100.0
        hits = sum(1 for row in self.rows if getattr(row, predicate))
        return round(100.0 * hits / len(self.rows), 3)

    @property
    def metrics(self) -> dict[str, float | int]:
        return {
            "discovery_coverage_pct": self._pct("discovered"),
            "raw_capture_coverage_pct": self._pct("raw_captured"),
            "hash_verification_pct": self._pct("hash_verified"),
            "extraction_coverage_pct": self._pct("extraction_complete"),
            "structural_accounting_pct": self._pct("structure_complete"),
            "provenance_coverage_pct": self._pct("provenance_complete"),
            "orphaned_source_elements": self.orphaned_elements,
            "unmanifested_outputs": self.unmanifested_outputs,
            "duplicate_canonical_objects": self.duplicate_objects,
            "paid_ingestion_llm_calls": self.llm_calls,
        }

    @property
    def passed(self) -> bool:
        m = self.metrics
        return (
            m["discovery_coverage_pct"] == 100.0
            and m["raw_capture_coverage_pct"] == 100.0
            and m["hash_verification_pct"] == 100.0
            and m["extraction_coverage_pct"] == 100.0
            and m["structural_accounting_pct"] == 100.0
            and m["provenance_coverage_pct"] == 100.0
            and m["orphaned_source_elements"] == 0
            and m["unmanifested_outputs"] == 0
            and m["duplicate_canonical_objects"] == 0
            and m["paid_ingestion_llm_calls"] == 0
        )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def elements_output_hash(elements: Sequence[SourceElement]) -> str:
    """A deterministic digest of a derived element tree (order, spans, types)."""
    payload = "\n".join(
        f"{e.sequence_index}|{e.element_type}|{e.level}|{e.parent_index}|"
        f"{e.raw_start}|{e.raw_end}|{_sha256(e.raw_text)}"
        for e in elements
    )
    return _sha256(payload)


def reconcile_object(
    entry: ManifestEntry,
    version: CapturedSourceVersion | None,
    elements: Sequence[SourceElement],
) -> LedgerRow:
    """One object's raw-to-extracted reconciliation, fully deterministic.

    ``elements`` is the CANDIDATE derivation (the current structurer run over the
    cached raw text) — reconciliation validates the derivation against the raw input
    via hash and full-coverage accounting, never against an LLM opinion.
    """
    failures: list[str] = []
    raw_captured = version is not None and version.raw_text is not None
    if not raw_captured:
        failures.append("no active raw capture")
    hash_verified = False
    extraction_complete = False
    structure_complete = False
    output_hash: str | None = None
    if version is not None:
        hash_verified = (
            version.content_hash is not None
            and raw_content_hash(version.raw_text) == version.content_hash
        )
        if not hash_verified:
            failures.append("raw text does not match its recorded content hash")
        # ``ingestion_status`` records the PERSISTED structure pass ('ok'/'failed',
        # None before any pass). The candidate run re-derives and re-verifies the
        # structure itself below, so only an explicit prior failure counts against
        # extraction here — a read failure never captures at all (``uncaptured``).
        extraction_complete = version.ingestion_status != INGESTION_FAILED
        if not extraction_complete:
            failures.append(f"extraction status is {version.ingestion_status!r}")
        violations = verify_full_coverage(version.raw_text, list(elements))
        structure_complete = not violations and (bool(elements) or not version.raw_text.strip())
        if violations:
            failures.extend(f"structure: {v}" for v in violations[:5])
        elif not structure_complete:
            failures.append("structure: no elements derived for non-empty raw text")
        else:
            output_hash = elements_output_hash(elements)
    provenance_complete = raw_captured and entry.raw_cache_location is not None
    if raw_captured and not provenance_complete:
        failures.append("no raw-cache provenance pointer")
    return LedgerRow(
        source_id=entry.source_id,
        path=entry.path,
        system=entry.system,
        discovered=True,  # every ledger row originates from a manifest entry
        raw_captured=raw_captured,
        hash_verified=hash_verified,
        extraction_complete=extraction_complete,
        structure_complete=structure_complete,
        element_count=len(elements),
        provenance_complete=provenance_complete,
        output_hash=output_hash,
        validation_status=LEDGER_OK if not failures else LEDGER_FAILED,
        failures=tuple(failures),
    )


def count_duplicates(entries: Sequence[ManifestEntry]) -> int:
    """Duplicate canonical objects: the same (system, path) manifested twice."""
    seen: dict[tuple[str, str], int] = {}
    for entry in entries:
        key = (entry.system, entry.path)
        seen[key] = seen.get(key, 0) + 1
    return sum(count - 1 for count in seen.values() if count > 1)


def count_orphaned_elements(
    stored_elements: Sequence[StoredSourceElement],
    known_version_ids: set[int],
) -> int:
    """Persisted SourceElements whose document version is not part of the universe."""
    return sum(1 for e in stored_elements if e.document_version_id not in known_version_ids)


__all__ = [
    "LEDGER_FAILED",
    "LEDGER_OK",
    "LedgerRow",
    "ManifestEntry",
    "ProjectCandidate",
    "ReconciliationReport",
    "SourceManifest",
    "build_manifest",
    "count_duplicates",
    "count_orphaned_elements",
    "elements_output_hash",
    "normalize_for_match",
    "reconcile_object",
]
