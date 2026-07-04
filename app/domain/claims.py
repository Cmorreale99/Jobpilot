"""Evidence-backed PAR claims: enums, state machine, and the two-pass extractor.

V2's core domain module. Encodes the non-negotiable definitions verbatim:

* **Problem** — a pain point costing business value. Valid only if it declares a
  ``cost_dimension`` (money|time|risk|quality|revenue), an ``inefficiency``
  (manual|slow|not_working|integration_difficulty|not_user_friendly), or both.
  A task description is NOT a problem.
* **Action** — steps the user took, naming concrete tools/tech. Any evidence
  source is valid for Actions.
* **Result** — business impact that resolves a pain point declared in the
  Problem. Any source may be searched for Results (content gate, not a
  source-type ban), but Result evidence must contain an OUTCOME statement
  ("cut failure rate from 12% to 0.5%"), never a WORK statement ("fixed dedup
  key mismatch"). Work statements are Action evidence only. A missing Result is
  recorded as ``result_kind=missing`` — never invented.

The extractor is **two-pass over the same sources**: pass 1 finds work
statements (Actions), pass 2 hunts specifically for outcome statements
(Results). The deterministic :class:`HeuristicTwoPassExtractor` here is the
mock-first default; the LLM-backed extractor (``app/llm/extraction.py``) is a
drop-in behind the same protocol. Either way the PAR validator
(``app/domain/par_validation.py``) is the gate — extraction is never trusted.

Pure logic: no imports from ``integrations/`` implementations or ``llm/``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.domain.applications import validate_transition

# --- Enums (values are the canonical DB strings) -----------------------------------


class ClaimStatus(StrEnum):
    """Lifecycle of one claim (``claims.status``)."""

    EXTRACTED = "extracted"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class CostDimension(StrEnum):
    """What a Problem costs the business (``claims.problem_cost_dimension``)."""

    MONEY = "money"
    TIME = "time"
    RISK = "risk"
    QUALITY = "quality"
    REVENUE = "revenue"


class Inefficiency(StrEnum):
    """How a Problem manifests (``claims.problem_inefficiency``)."""

    MANUAL = "manual"
    SLOW = "slow"
    NOT_WORKING = "not_working"
    INTEGRATION_DIFFICULTY = "integration_difficulty"
    NOT_USER_FRIENDLY = "not_user_friendly"


class ResultKind(StrEnum):
    """How a Result is evidenced (``claims.result_kind``)."""

    QUANTIFIED = "quantified"
    QUALITATIVE_EVIDENCED = "qualitative_evidenced"
    MISSING = "missing"


class ResultStatus(StrEnum):
    """Verification level of a Result (``claims.result_status``)."""

    VERIFIED = "verified"
    USER_ATTESTED = "user_attested"
    UNVERIFIED = "unverified"


class ClaimField(StrEnum):
    """Which PAR field a claim_evidence link supports (``claim_evidence.field``)."""

    PROBLEM = "problem"
    ACTION = "action"
    RESULT = "result"


class ExperienceSection(StrEnum):
    """Which resume section an experience renders under (``experiences.section``)."""

    PROFESSIONAL_EXPERIENCE = "professional_experience"
    PROJECTS_HACKATHONS = "projects_hackathons"


# Evidence source types (documented values; deliberately NOT a CHECK constraint —
# the content gate lives in the PAR validator, where it belongs).
SOURCE_DRIVE = "drive"
SOURCE_GITHUB_COMMIT = "github_commit"
SOURCE_GITHUB_PR = "github_pr"
SOURCE_GITHUB_README = "github_readme"
SOURCE_UPLOAD = "upload"
SOURCE_USER_ATTESTATION = "user_attestation"


# --- State machine ------------------------------------------------------------------

# extracted -> pending_review -> approved | rejected. Terminal states map to the
# empty set: re-litigating a decision is a new extraction, not a status edit.
CLAIM_TRANSITIONS: dict[ClaimStatus, frozenset[ClaimStatus]] = {
    ClaimStatus.EXTRACTED: frozenset({ClaimStatus.PENDING_REVIEW}),
    ClaimStatus.PENDING_REVIEW: frozenset({ClaimStatus.APPROVED, ClaimStatus.REJECTED}),
    ClaimStatus.APPROVED: frozenset(),
    ClaimStatus.REJECTED: frozenset(),
}


class RejectionReasonRequiredError(ValueError):
    """Raised when a claim is rejected without a reason."""


def validate_claim_transition(
    current: ClaimStatus, new: ClaimStatus, *, review_note: str | None = None
) -> None:
    """Validate a claim status change; rejecting requires a non-empty reason."""
    validate_transition(CLAIM_TRANSITIONS, current, new)
    if new is ClaimStatus.REJECTED and not (review_note or "").strip():
        raise RejectionReasonRequiredError("rejecting a claim requires a reason (review_note)")


# --- Data shapes --------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceChunk:
    """One citable unit of evidence (a commit message, a README, a Drive doc)."""

    source_type: str
    source_ref: str
    chunk_text: str


@dataclass(frozen=True)
class ExperienceSeed:
    """The experience descriptor an evidence group belongs to (pre-persistence)."""

    name: str
    section: ExperienceSection
    subtitle: str | None = None
    dates: str | None = None


@dataclass(frozen=True)
class EvidenceGroup:
    """All evidence chunks for one experience — the extractor's unit of work."""

    experience: ExperienceSeed
    chunks: tuple[EvidenceChunk, ...]


@dataclass(frozen=True)
class ClaimEvidenceRef:
    """A draft claim's citation: which chunk supports which PAR field.

    ``outcome_quote`` is required when ``field=result`` — the verbatim outcome
    statement inside the cited chunk (the validator enforces both).
    """

    chunk: EvidenceChunk
    field: ClaimField
    outcome_quote: str | None = None


@dataclass(frozen=True)
class DraftClaim:
    """An extracted-but-unvalidated PAR claim, before persistence."""

    action_text: str
    action_tools: tuple[str, ...] = ()
    problem_text: str | None = None
    problem_cost_dimension: CostDimension | None = None
    problem_inefficiency: Inefficiency | None = None
    result_text: str | None = None
    result_kind: ResultKind = ResultKind.MISSING
    result_metric_json: dict[str, Any] | None = None
    evidence: tuple[ClaimEvidenceRef, ...] = ()


@dataclass(frozen=True)
class Experience:
    """A persisted experience (groups claims; user assigns section + order in review)."""

    id: int
    user_id: str
    name: str
    section: ExperienceSection
    subtitle: str | None = None
    dates: str | None = None
    sort_order: int = 0


@dataclass(frozen=True)
class StoredEvidence:
    """A persisted evidence chunk."""

    id: int
    user_id: str
    source_type: str
    source_ref: str
    chunk_text: str
    created_at: datetime | None = None


@dataclass(frozen=True)
class ClaimEvidenceLink:
    """A persisted claim_evidence row."""

    evidence_id: int
    field: ClaimField
    outcome_quote: str | None = None


@dataclass(frozen=True)
class Claim:
    """A persisted claim with provenance links and validator flags."""

    id: int
    user_id: str
    experience_id: int
    status: ClaimStatus
    action_text: str
    action_tools: tuple[str, ...] = ()
    problem_text: str | None = None
    problem_cost_dimension: CostDimension | None = None
    problem_inefficiency: Inefficiency | None = None
    result_text: str | None = None
    result_kind: ResultKind = ResultKind.MISSING
    result_status: ResultStatus = ResultStatus.UNVERIFIED
    result_metric_json: dict[str, Any] | None = None
    validation_flags: tuple[str, ...] = ()
    review_note: str | None = None
    reviewed_at: datetime | None = None
    evidence: tuple[ClaimEvidenceLink, ...] = ()


# --- Extractor protocol -------------------------------------------------------------


class ClaimExtractionError(RuntimeError):
    """Extraction failed outright for one evidence group (e.g. the LLM call failed).

    Raised instead of silently degrading to a different extractor: the caller skips
    the group loudly (existing claims stay untouched) and records the failure.
    """


@runtime_checkable
class ClaimExtractor(Protocol):
    """Two-pass PAR extraction over one experience's evidence.

    ``violations`` carries the PAR validator's specific findings on a bounced
    re-extraction (empty on the first pass). The deterministic extractor ignores
    it (same input, same output); the LLM extractor folds it into the prompt.

    ``extract`` may raise :class:`ClaimExtractionError` when it cannot produce an
    answer at all — it must never substitute a different extractor's output.
    """

    def extract(self, group: EvidenceGroup, violations: Sequence[str] = ()) -> list[DraftClaim]: ...


# --- Deterministic two-pass extractor ------------------------------------------------
#
# Statement classification, applied in this order (first match wins):
#   1. explicit ``Problem:`` / ``Action:`` / ``Result:`` labels
#   2. OUTCOME statement — an outcome verb plus a metric pattern (quantified), or a
#      qualitative outcome marker ("no longer", "eliminated ...")
#   3. WORK statement — a leading work/outcome verb without outcome evidence
#      ("Improved reliability by engineering ..." is work, not an outcome)
#   4. PROBLEM statement — pain-point lexicon hit (cost dimension or inefficiency)
#   5. anything else is ignored, never turned into a claim
#
# Rule 3 deliberately encodes the outcome-statement rule: a verb-led sentence with no
# metric and no qualitative marker is a work statement and can only ever be Action
# evidence. The V1 bug — work statements pasted into Result fields — is unrepresentable.

_LABEL_RE = re.compile(r"^(problem|action|result)\s*:\s*(.+)$", re.IGNORECASE)
_BULLET_RE = re.compile(r"^[-*•]\s+")

# Metric patterns: "from A to B" (A/B up to a few words each, e.g. "from 10 hours
# to 1 hour"), percentages, dollar amounts, "by <number>".
_METRIC_RE = re.compile(
    r"(\bfrom\s+\S+(?:\s+\S+){0,4}?\s+to\s+\S+)"
    r"|(\d+(?:\.\d+)?\s*%)"
    r"|(\$[\d,.]+\s*[kKmMbB]?)"
    r"|(\bby\s+~?\d)"
)

_OUTCOME_VERBS = frozenset(
    {
        "reduced",
        "cut",
        "increased",
        "improved",
        "decreased",
        "lowered",
        "grew",
        "boosted",
        "saved",
        "accelerated",
        "shortened",
        "eliminated",
        "doubled",
        "halved",
    }
)

# Qualitative outcome markers: state-change phrases that evidence an outcome without
# a number ("ops no longer reconciles manually").
_QUALITATIVE_MARKERS = ("no longer", "eliminated", "without manual", "unblocked")

_WORK_VERBS = frozenset(
    {
        "built",
        "build",
        "fixed",
        "fix",
        "implemented",
        "implement",
        "added",
        "add",
        "created",
        "create",
        "engineered",
        "engineer",
        "migrated",
        "migrate",
        "refactored",
        "refactor",
        "designed",
        "design",
        "wrote",
        "write",
        "developed",
        "develop",
        "integrated",
        "integrate",
        "parallelized",
        "parallelize",
        "automated",
        "automate",
        "diagnosed",
        "diagnose",
        "resolved",
        "resolve",
        "owned",
        "architected",
        "spearheaded",
        "reconstructed",
        "reverse-engineered",
        "mapped",
        "deployed",
        "deploy",
        "instrumented",
        "rewrote",
        "backfilled",
        "reworked",
        "replaced",
        "introduced",
        "extracted",
        "structured",
    }
)

# Concrete tools/tech the deterministic extractor can recognize in an Action.
# Extend as fixtures grow; the LLM extractor names tools itself (validator-gated).
_TOOL_LEXICON = (
    "Python",
    "Pandas",
    "NumPy",
    "SQL",
    "Bash",
    "React",
    "TypeScript",
    "JavaScript",
    "Git",
    "GitHub Actions",
    "Kafka",
    "Docker",
    "Kubernetes",
    "Terraform",
    "AWS",
    "Lambda",
    "S3",
    "DynamoDB",
    "Oracle",
    "Postgres",
    "PostgreSQL",
    "MySQL",
    "Redis",
    "Airflow",
    "dbt",
    "Spark",
    "Snowflake",
    "Databricks",
    "FastAPI",
    "Django",
    "Flask",
    "aiohttp",
    "REST",
    "GraphQL",
    "Excel",
    "Go",
    "Rust",
    "Java",
    "Jenkins",
    "Grafana",
    "Prometheus",
)

_COST_DIMENSION_KEYWORDS: tuple[tuple[CostDimension, tuple[str, ...]], ...] = (
    (CostDimension.MONEY, ("$", "cost", "budget", "spend", "expensive", "saving")),
    (CostDimension.TIME, ("hour", "hrs", "minute", "week", "day", "time", "latency")),
    (CostDimension.RISK, ("risk", "outage", "incident", "breach", "compliance", "downtime")),
    (
        CostDimension.QUALITY,
        ("failure", "error", "defect", "duplicate", "inaccura", "unreliab", "hallucinat", "bug"),
    ),
    (CostDimension.REVENUE, ("revenue", "sales", "churn", "conversion")),
)

_INEFFICIENCY_KEYWORDS: tuple[tuple[Inefficiency, tuple[str, ...]], ...] = (
    (Inefficiency.MANUAL, ("manual", "by hand", "spreadsheet", "excel workflow")),
    (Inefficiency.SLOW, ("slow", "bottleneck", "took", "taking")),
    (Inefficiency.NOT_WORKING, ("broken", "failing", "not working", "crash", "does not work")),
    (
        Inefficiency.INTEGRATION_DIFFICULTY,
        ("integration", "incompatib", "siloed", "undocumented"),
    ),
    (Inefficiency.NOT_USER_FRIENDLY, ("confusing", "unusable", "hard to use", "unintuitive")),
)


def _first_word(statement: str) -> str:
    match = re.match(r"[A-Za-z-]+", statement)
    return match.group(0).lower() if match else ""


def _is_outcome(statement: str) -> tuple[bool, ResultKind]:
    """Classify an outcome statement: (is_outcome, quantified|qualitative_evidenced)."""
    lowered = statement.lower()
    has_metric = _METRIC_RE.search(statement) is not None
    words = frozenset(re.findall(r"[a-z-]+", lowered))
    has_outcome_verb = bool(words & _OUTCOME_VERBS)
    if has_metric and has_outcome_verb:
        return True, ResultKind.QUANTIFIED
    if any(marker in lowered for marker in _QUALITATIVE_MARKERS) and not has_metric:
        return True, ResultKind.QUALITATIVE_EVIDENCED
    return False, ResultKind.MISSING


def _is_work_statement(statement: str) -> bool:
    return _first_word(statement) in (_WORK_VERBS | _OUTCOME_VERBS)


def _find_tools(statement: str) -> tuple[str, ...]:
    lowered = statement.lower()
    found = []
    for tool in _TOOL_LEXICON:
        if re.search(rf"(?<![\w-]){re.escape(tool.lower())}(?![\w-])", lowered):
            found.append(tool)
    return tuple(found)


def _problem_dimensions(
    statement: str,
) -> tuple[CostDimension | None, Inefficiency | None]:
    lowered = statement.lower()
    cost = next(
        (dim for dim, keys in _COST_DIMENSION_KEYWORDS if any(k in lowered for k in keys)),
        None,
    )
    inefficiency = next(
        (ineff for ineff, keys in _INEFFICIENCY_KEYWORDS if any(k in lowered for k in keys)),
        None,
    )
    return cost, inefficiency


def _is_problem_statement(statement: str) -> bool:
    cost, inefficiency = _problem_dimensions(statement)
    return cost is not None or inefficiency is not None


def derive_resolves(
    outcome_text: str,
    declared: Sequence[CostDimension | Inefficiency],
) -> str | None:
    """Which declared pain point an outcome resolves — or its own best lexicon match.

    Prefers a *declared* dimension/inefficiency whose keywords appear in the outcome
    text (an honest coupling). Falls back to the first lexicon hit overall — which the
    validator will then flag as "result does not address stated problem" when it is
    not among the declared values. Returns ``None`` when nothing matches.
    """
    lowered = outcome_text.lower()
    keyword_map: dict[str, tuple[str, ...]] = {
        **{dim.value: keys for dim, keys in _COST_DIMENSION_KEYWORDS},
        **{ineff.value: keys for ineff, keys in _INEFFICIENCY_KEYWORDS},
    }
    for value in declared:
        if value is not None and any(k in lowered for k in keyword_map[value.value]):
            return value.value
    for name, keys in keyword_map.items():
        if any(k in lowered for k in keys):
            return name
    return None


@dataclass
class _Statement:
    """A verbatim span of a chunk, classified."""

    text: str
    chunk: EvidenceChunk
    label: str | None = None  # explicit problem/action/result label, if any


def _split_statements(chunk: EvidenceChunk) -> list[_Statement]:
    """Split a chunk into verbatim statements (lines, minus bullet markers).

    Statements must remain exact substrings of ``chunk_text`` so quotes taken from
    them satisfy the verbatim checks. Bullet markers are stripped (the remainder is
    still a substring); headers and blank lines are dropped.
    """
    statements: list[_Statement] = []
    for raw_line in chunk.chunk_text.splitlines():
        line = _BULLET_RE.sub("", raw_line.strip())
        if not line or line.startswith("#"):
            continue
        label_match = _LABEL_RE.match(line)
        if label_match:
            statements.append(
                _Statement(
                    text=label_match.group(2).strip(),
                    chunk=chunk,
                    label=label_match.group(1).lower(),
                )
            )
        else:
            statements.append(_Statement(text=line, chunk=chunk))
    return statements


@dataclass
class _ClaimAccumulator:
    """Mutable claim being assembled across the two passes."""

    action: _Statement
    tools: tuple[str, ...]
    problem: _Statement | None = None
    outcome: _Statement | None = None
    outcome_kind: ResultKind = ResultKind.MISSING

    def to_draft(self) -> DraftClaim:
        cost, inefficiency = (
            _problem_dimensions(self.problem.text) if self.problem else (None, None)
        )
        evidence: list[ClaimEvidenceRef] = [
            ClaimEvidenceRef(chunk=self.action.chunk, field=ClaimField.ACTION)
        ]
        if self.problem is not None:
            evidence.append(ClaimEvidenceRef(chunk=self.problem.chunk, field=ClaimField.PROBLEM))

        result_text: str | None = None
        result_metric_json: dict[str, Any] | None = None
        if self.outcome is not None:
            result_text = self.outcome.text
            declared = [d for d in (cost, inefficiency) if d is not None]
            resolves = derive_resolves(self.outcome.text, declared)
            result_metric_json = {"resolves": resolves}
            if self.outcome_kind is ResultKind.QUANTIFIED:
                result_metric_json["metric_text"] = self.outcome.text
            evidence.append(
                ClaimEvidenceRef(
                    chunk=self.outcome.chunk,
                    field=ClaimField.RESULT,
                    outcome_quote=self.outcome.text,
                )
            )

        return DraftClaim(
            action_text=self.action.text,
            action_tools=self.tools,
            problem_text=self.problem.text if self.problem else None,
            problem_cost_dimension=cost,
            problem_inefficiency=inefficiency,
            result_text=result_text,
            result_kind=self.outcome_kind if self.outcome else ResultKind.MISSING,
            result_metric_json=result_metric_json,
            evidence=tuple(evidence),
        )


class HeuristicTwoPassExtractor:
    """Deterministic two-pass PAR extractor — the mock-first default.

    Pass 1 walks each chunk in order, collecting work statements into claims and
    tracking the most recent problem statement as their Problem context. Pass 2
    hunts outcome statements: one co-located with (or following) a claim in its
    chunk attaches there; outcome statements in chunks with no work statement are
    attached FIFO to claims still missing a Result. Nothing is ever invented — a
    claim with no outcome statement anywhere keeps ``result_kind=missing``.
    """

    def extract(self, group: EvidenceGroup, violations: Sequence[str] = ()) -> list[DraftClaim]:
        del violations  # deterministic: re-extraction cannot produce a different answer
        claims: list[_ClaimAccumulator] = []
        orphan_outcomes: list[tuple[_Statement, ResultKind]] = []

        # Pass 1 — work statements become Actions (with problem context per chunk).
        for chunk in group.chunks:
            current_problem: _Statement | None = None
            chunk_claims: list[_ClaimAccumulator] = []
            for statement in _split_statements(chunk):
                role = self._classify(statement)
                if role == "problem":
                    # Problem context applies forward only: pairing a later problem
                    # with an earlier action would be inventing a coupling.
                    current_problem = statement
                elif role == "work":
                    chunk_claims.append(
                        _ClaimAccumulator(
                            action=statement,
                            tools=_find_tools(statement.text),
                            problem=current_problem,
                        )
                    )
            claims.extend(chunk_claims)

        # Pass 2 — hunt outcome statements and attach them as Results.
        for chunk in group.chunks:
            chunk_claim_queue = [c for c in claims if c.action.chunk is chunk]
            for statement in _split_statements(chunk):
                if self._classify(statement) != "outcome":
                    continue
                _, kind = _is_outcome(statement.text)
                target = next((c for c in chunk_claim_queue if c.outcome is None), None)
                if target is not None:
                    target.outcome = statement
                    target.outcome_kind = kind
                else:
                    orphan_outcomes.append((statement, kind))

        for statement, kind in orphan_outcomes:
            target = next((c for c in claims if c.outcome is None), None)
            if target is None:
                break
            target.outcome = statement
            target.outcome_kind = kind

        return [claim.to_draft() for claim in claims]

    @classmethod
    def _classify(cls, statement: _Statement) -> str:
        """Classify one statement as outcome / work / problem / ignored."""
        if statement.label == "problem":
            return "problem" if _is_problem_statement(statement.text) else "ignored"
        if statement.label == "action":
            return "work"
        if statement.label == "result":
            is_outcome, _ = _is_outcome(statement.text)
            return "outcome" if is_outcome else "ignored"
        is_outcome, _ = _is_outcome(statement.text)
        if is_outcome:
            return "outcome"
        if _is_work_statement(statement.text):
            return "work"
        if _is_problem_statement(statement.text):
            return "problem"
        return "ignored"


# --- Persistence contract ------------------------------------------------------------


@dataclass(frozen=True)
class StorableClaim:
    """A validated draft plus its post-validation status, ready to persist."""

    draft: DraftClaim
    status: ClaimStatus
    result_status: ResultStatus
    validation_flags: tuple[str, ...] = ()


@runtime_checkable
class ClaimRepository(Protocol):
    """Persistence for experiences, evidence, claims, and their links.

    Write semantics shared by every implementation:

    * ``upsert_experience`` dedupes on ``(user_id, name)``; an existing row keeps its
      user-assigned ``section``/``sort_order`` (re-extraction never overrides review).
    * ``upsert_evidence`` dedupes on ``(user_id, source_type, source_ref)``, updating
      ``chunk_text`` in place when the source changed.
    * ``replace_unreviewed_claims`` is the idempotency rule: it replaces this
      experience's ``extracted``/``pending_review`` claims with the new batch but
      never touches ``approved``/``rejected`` rows, and skips inserting a claim whose
      content matches an already-reviewed one (a human decision is never re-queued).
    * ``transition_claim`` validates against :data:`CLAIM_TRANSITIONS` and requires a
      ``review_note`` on rejection.
    """

    def upsert_experience(self, user_id: str, seed: ExperienceSeed) -> Experience: ...

    def upsert_evidence(self, user_id: str, chunk: EvidenceChunk) -> StoredEvidence: ...

    def get_evidence(self, evidence_id: int) -> StoredEvidence | None: ...

    def replace_unreviewed_claims(
        self, user_id: str, experience_id: int, claims: Sequence[StorableClaim]
    ) -> list[Claim]: ...

    def get_claim(self, claim_id: int) -> Claim | None: ...

    def list_claims(self, user_id: str, status: ClaimStatus | None = None) -> list[Claim]: ...

    def list_experiences(self, user_id: str) -> list[Experience]: ...

    def transition_claim(
        self,
        claim_id: int,
        new_status: ClaimStatus,
        *,
        review_note: str | None = None,
        reviewed_at: datetime | None = None,
    ) -> Claim: ...

    def apply_claim_edit(self, user_id: str, plan: ClaimEditPlan) -> Claim:
        """Persist an edit plan: upsert attestation evidence, add links, apply updates."""
        ...

    def update_experience_layout(
        self,
        experience_id: int,
        *,
        section: ExperienceSection | None = None,
        sort_order: int | None = None,
    ) -> Experience:
        """The section picker: reassign where an experience renders, and its order."""
        ...


def evidence_source_url(source_type: str, source_ref: str) -> str | None:
    """The click-through URL for one evidence source, when one exists.

    Deterministic mapping from stored provenance to the place a human can read it:
    GitHub READMEs/commits and Drive files resolve; uploads and user attestations are
    local by nature and return ``None`` (the review card shows the quote itself).
    """
    ref = source_ref.strip()
    if not ref:
        return None
    if source_type == SOURCE_GITHUB_README:
        return f"https://github.com/{ref}"
    if source_type == SOURCE_GITHUB_COMMIT and "@" in ref:
        repo_ref, _, sha = ref.rpartition("@")
        if repo_ref and sha:
            return f"https://github.com/{repo_ref}/commit/{sha}"
        return None
    if source_type == SOURCE_GITHUB_PR and "#" in ref:
        repo_ref, _, number = ref.rpartition("#")
        if repo_ref and number.isdigit():
            return f"https://github.com/{repo_ref}/pull/{number}"
        return None
    if source_type == SOURCE_DRIVE:
        return f"https://drive.google.com/open?id={ref}"
    return None


def claim_content_fingerprint(
    problem_text: str | None, action_text: str, result_text: str | None
) -> tuple[str, str, str]:
    """Stable identity of a claim's substance, for reviewed-claim dedupe on re-runs."""

    def norm(text: str | None) -> str:
        return " ".join((text or "").split()).lower()

    return (norm(problem_text), norm(action_text), norm(result_text))


# --- Review edits (edit-then-approve) --------------------------------------------------


class ClaimEditError(ValueError):
    """Raised when a requested edit is inconsistent or the claim cannot be edited."""


@dataclass(frozen=True)
class ClaimEdits:
    """The fields a reviewer may change before approving. ``None`` means untouched.

    Result edits are how the Missing-Results queue resolves: the reviewer supplies
    the impact statement (``result_text`` + explicit ``result_kind``); the result
    becomes ``user_attested``. Approving without edits is approval to omit.
    """

    problem_text: str | None = None
    problem_cost_dimension: CostDimension | None = None
    problem_inefficiency: Inefficiency | None = None
    action_text: str | None = None
    action_tools: tuple[str, ...] | None = None
    result_text: str | None = None
    result_kind: ResultKind | None = None
    result_metric_json: dict[str, Any] | None = None

    def is_empty(self) -> bool:
        return all(
            value is None
            for value in (
                self.problem_text,
                self.problem_cost_dimension,
                self.problem_inefficiency,
                self.action_text,
                self.action_tools,
                self.result_text,
                self.result_kind,
                self.result_metric_json,
            )
        )


@dataclass(frozen=True)
class AttestationLink:
    """A user_attestation evidence row to create, plus the claim link to add."""

    chunk: EvidenceChunk
    field: ClaimField
    outcome_quote: str | None = None


@dataclass(frozen=True)
class ClaimEditPlan:
    """Everything a repository must persist for one edit, computed purely.

    ``updates`` maps claim attribute names to their new values. ``attestations``
    are the provenance rows: every edited field group gets a ``user_attestation``
    evidence row holding the attested text, linked to the claim on that field —
    so traceability survives the edit. ``validation_flags`` is always cleared:
    the human decision supersedes the validator's findings.
    """

    claim_id: int
    updates: dict[str, Any]
    attestations: tuple[AttestationLink, ...]


def _attestation_chunk(claim_id: int, field: ClaimField, text: str) -> EvidenceChunk:
    # source_ref is stable per (claim, field): a re-edit updates the same row rather
    # than accreting near-duplicates (edits only happen before the terminal approve).
    return EvidenceChunk(
        source_type=SOURCE_USER_ATTESTATION,
        source_ref=f"claim:{claim_id}:{field.value}",
        chunk_text=text,
    )


def plan_claim_edit(claim: Claim, edits: ClaimEdits) -> ClaimEditPlan:
    """Compute the persistence plan for an edit-then-approve.

    Only ``pending_review`` claims are editable. Any edit inside a PAR field group
    (problem text/dimensions, action text/tools, result text/kind/metric) attests
    that whole group: the resulting text is stored as a ``user_attestation``
    evidence row and linked on that field. An edited Result additionally becomes
    ``result_status=user_attested``.
    """
    if claim.status is not ClaimStatus.PENDING_REVIEW:
        raise ClaimEditError(f"only pending_review claims can be edited (status: {claim.status})")
    if edits.is_empty():
        raise ClaimEditError("no edits supplied; use a plain approve instead")
    if edits.result_kind is not None and edits.result_kind is ResultKind.MISSING:
        raise ClaimEditError("an edit cannot set result_kind=missing; reject or approve as-is")
    if edits.result_text is not None and edits.result_kind is None:
        raise ClaimEditError(
            "supplying result_text requires an explicit result_kind "
            "(quantified or qualitative_evidenced)"
        )
    if edits.result_kind is not None and edits.result_text is None and claim.result_text is None:
        raise ClaimEditError("result_kind without result_text on a claim that has no Result")

    updates: dict[str, Any] = {"validation_flags": ()}
    attestations: list[AttestationLink] = []

    problem_edited = any(
        value is not None
        for value in (edits.problem_text, edits.problem_cost_dimension, edits.problem_inefficiency)
    )
    if problem_edited:
        problem_text = edits.problem_text if edits.problem_text is not None else claim.problem_text
        if not (problem_text or "").strip():
            raise ClaimEditError("cannot attest problem dimensions on a claim with no Problem text")
        updates["problem_text"] = problem_text
        if edits.problem_cost_dimension is not None:
            updates["problem_cost_dimension"] = edits.problem_cost_dimension
        if edits.problem_inefficiency is not None:
            updates["problem_inefficiency"] = edits.problem_inefficiency
        attestations.append(
            AttestationLink(
                chunk=_attestation_chunk(claim.id, ClaimField.PROBLEM, problem_text or ""),
                field=ClaimField.PROBLEM,
            )
        )

    action_edited = edits.action_text is not None or edits.action_tools is not None
    if action_edited:
        action_text = edits.action_text if edits.action_text is not None else claim.action_text
        if not action_text.strip():
            raise ClaimEditError("action_text cannot be emptied")
        updates["action_text"] = action_text
        if edits.action_tools is not None:
            updates["action_tools"] = edits.action_tools
        attestations.append(
            AttestationLink(
                chunk=_attestation_chunk(claim.id, ClaimField.ACTION, action_text),
                field=ClaimField.ACTION,
            )
        )

    result_edited = any(
        value is not None
        for value in (edits.result_text, edits.result_kind, edits.result_metric_json)
    )
    if result_edited:
        result_text = edits.result_text if edits.result_text is not None else claim.result_text
        if not (result_text or "").strip():
            raise ClaimEditError("an edited Result needs non-empty result_text")
        updates["result_text"] = result_text
        updates["result_kind"] = edits.result_kind or claim.result_kind
        updates["result_status"] = ResultStatus.USER_ATTESTED
        if edits.result_metric_json is not None:
            updates["result_metric_json"] = edits.result_metric_json
        attestations.append(
            AttestationLink(
                chunk=_attestation_chunk(claim.id, ClaimField.RESULT, result_text or ""),
                field=ClaimField.RESULT,
                outcome_quote=result_text,
            )
        )

    return ClaimEditPlan(claim_id=claim.id, updates=updates, attestations=tuple(attestations))


__all__ = [
    "CLAIM_TRANSITIONS",
    "AttestationLink",
    "Claim",
    "ClaimEditError",
    "ClaimEditPlan",
    "ClaimEdits",
    "ClaimEvidenceLink",
    "ClaimEvidenceRef",
    "ClaimExtractionError",
    "ClaimExtractor",
    "ClaimField",
    "ClaimRepository",
    "ClaimStatus",
    "CostDimension",
    "DraftClaim",
    "EvidenceChunk",
    "EvidenceGroup",
    "Experience",
    "ExperienceSection",
    "ExperienceSeed",
    "HeuristicTwoPassExtractor",
    "Inefficiency",
    "RejectionReasonRequiredError",
    "ResultKind",
    "ResultStatus",
    "SOURCE_DRIVE",
    "SOURCE_GITHUB_COMMIT",
    "SOURCE_GITHUB_PR",
    "SOURCE_GITHUB_README",
    "SOURCE_UPLOAD",
    "SOURCE_USER_ATTESTATION",
    "StorableClaim",
    "StoredEvidence",
    "claim_content_fingerprint",
    "derive_resolves",
    "evidence_source_url",
    "plan_claim_edit",
    "validate_claim_transition",
]
