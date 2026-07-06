"""The live-shaped corpus fixture — production's exact shape, offline.

V2's documented eval failure (docs/V2_AUDIT.md §13) was testing green on fixtures no
real document resembles. docs/ARCHITECTURE_V3.md §5 therefore makes this fixture
Phase 2's first commit: a corpus matching the **audited live database** (docs/
V3_AUDIT.md, settled corpus facts, 2026-07-05) so every story-layer exit test runs
against the shape the code will actually meet:

* 15 confirmed entities — 12 with pending claims, 3 claim-less (``Jobpilot`` with 116
  evidence chunks and zero claims, ``DS4635`` with 10 chunks, ``Paper recommender
  system`` with no evidence at all).
* 148 pending claims with the real per-entity Problem/Result distribution
  (``total/with-problem/with-result``): 28/2/0, 20/3/7, 16/5/5, 16/7/1, 12/4/7,
  11/3/6, 11/2/2, 11/7/5, 8/0/1, 7/1/0, 5/0/2, 3/3/0.
* One approved claim (on ``investment-decision-workflow-engine`` — its Problem is a
  one-line ``dashboard.py`` bug, live claim 373's shape) and one rejected claim.
* The live cross-entity duplicate: the same outcome quote ("Top 3 out of 100+ teams")
  backing a Result under both ``OneWorld`` and the portfolio container entity.
* The cross-source metric conflict: "cutting export failures in half" vs. "… in half
  across all regions" under one entity, from two different resume exports.
* ``personal-knowledge-os`` / ``AI-Recovery-navigation`` "Problems" that are one-line
  code bugs (file names) — present at claim level, failing V3's problem-space bar.

Everything is deterministic and built through the ordinary repository writes, so the
fixture also exercises the same persistence semantics production uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.claims import (
    SOURCE_DRIVE,
    SOURCE_GITHUB_COMMIT,
    SOURCE_GITHUB_README,
    Claim,
    ClaimEvidenceRef,
    ClaimField,
    ClaimRepository,
    ClaimStatus,
    CostDimension,
    DraftClaim,
    EvidenceChunk,
    Experience,
    ExperienceKind,
    ExperienceSection,
    ExperienceSeed,
    ExperienceStatus,
    Inefficiency,
    ResultKind,
    ResultStatus,
    StorableClaim,
    span_ref,
)
from app.domain.par_validation import COUPLING_FLAG
from app.services.claim_repository import InMemoryClaimRepository

LIVE_USER = "live-user"

# The live proof-case quote sitting under two entities (audit: claims 472/513).
SHARED_OUTCOME_QUOTE = "Top 3 out of 100+ teams"

# The cross-source conflict pair (audit red-team 8; the roster fixture wording).
CONFLICT_QUOTE_V1 = "cutting export failures in half"
CONFLICT_QUOTE_V2 = "cutting export failures in half across all regions"


@dataclass(frozen=True)
class EntitySpec:
    """One live entity's shape: ``claims/with_problem/with_result`` per the audit."""

    name: str
    kind: ExperienceKind
    section: ExperienceSection
    claims: int
    with_problem: int
    with_result: int
    topic: str  # domain noun keeping this entity's generated text distinct
    aliases: tuple[str, ...] = ()
    extra_chunks: int = 0  # evidence chunks beyond the ones claims cite
    bug_line_problems: tuple[str, ...] = ()  # code-bug "Problems" failing the V3 bar


ENTITY_SPECS: tuple[EntitySpec, ...] = (
    EntitySpec(
        "MassDEP",
        ExperienceKind.EMPLOYER_ROLE,
        ExperienceSection.PROFESSIONAL_EXPERIENCE,
        20,
        3,
        7,
        topic="permit",
    ),
    EntitySpec(
        "Wellington Management",
        ExperienceKind.EMPLOYER_ROLE,
        ExperienceSection.PROFESSIONAL_EXPERIENCE,
        16,
        5,
        5,
        topic="holdings",
    ),
    EntitySpec(
        "OneWorld",
        ExperienceKind.PROJECT,
        ExperienceSection.PROJECTS_HACKATHONS,
        16,
        7,
        1,
        topic="translation",
        aliases=("cmorreale99/oneworld",),
    ),
    EntitySpec(
        "cameron-morreale-portfolio",
        ExperienceKind.PROJECT,
        ExperienceSection.PROJECTS_HACKATHONS,
        12,
        4,
        7,
        topic="portfolio",
        aliases=("cmorreale99/cameron-morreale-portfolio",),
    ),
    EntitySpec(
        "Embue",
        ExperienceKind.EMPLOYER_ROLE,
        ExperienceSection.PROFESSIONAL_EXPERIENCE,
        11,
        3,
        6,
        topic="telemetry",
    ),
    EntitySpec(
        "Oasis",
        ExperienceKind.EMPLOYER_ROLE,
        ExperienceSection.PROFESSIONAL_EXPERIENCE,
        11,
        2,
        2,
        topic="intake",
    ),
    EntitySpec(
        "BoardGameGeek",
        ExperienceKind.PROJECT,
        ExperienceSection.PROJECTS_HACKATHONS,
        11,
        7,
        5,
        topic="ratings",
        aliases=("cmorreale99/boardgamegeek-analytics",),
    ),
    EntitySpec(
        "investment-decision-workflow-engine",
        ExperienceKind.PROJECT,
        ExperienceSection.PROJECTS_HACKATHONS,
        8,
        0,
        1,
        topic="screening",
        aliases=("cmorreale99/investment-decision-workflow-engine",),
    ),
    EntitySpec(
        "Stock Market Simulation",
        ExperienceKind.PROJECT,
        ExperienceSection.PROJECTS_HACKATHONS,
        5,
        0,
        2,
        topic="simulation",
        aliases=("cmorreale99/stock-market-simulation",),
    ),
    EntitySpec(
        "Cooper.ai",
        ExperienceKind.PROJECT,
        ExperienceSection.PROJECTS_HACKATHONS,
        3,
        3,
        0,
        topic="onboarding",
        aliases=("cmorreale99/cooper-ai",),
    ),
    EntitySpec(
        "personal-knowledge-os",
        ExperienceKind.PROJECT,
        ExperienceSection.PROJECTS_HACKATHONS,
        28,
        2,
        0,
        topic="notes",
        aliases=("cmorreale99/personal-knowledge-os",),
        bug_line_problems=(
            "pgvector lookup in retrieval.py returned stale rows",
            "binary embedding cache in store.py truncated vectors",
        ),
    ),
    EntitySpec(
        "AI-Recovery-navigation",
        ExperienceKind.PROJECT,
        ExperienceSection.PROJECTS_HACKATHONS,
        7,
        1,
        0,
        topic="recovery",
        aliases=("cmorreale99/ai-recovery-navigation",),
        bug_line_problems=("the .gitignore excluded the fixtures directory needed at runtime",),
    ),
    # The three claim-less entities (the audit's 15-not-12 correction).
    EntitySpec(
        "Jobpilot",
        ExperienceKind.PROJECT,
        ExperienceSection.PROJECTS_HACKATHONS,
        0,
        0,
        0,
        topic="jobsearch",
        aliases=("cmorreale99/jobpilot",),
        extra_chunks=116,
    ),
    EntitySpec(
        "DS4635",
        ExperienceKind.PROJECT,
        ExperienceSection.PROJECTS_HACKATHONS,
        0,
        0,
        0,
        topic="coursework",
        extra_chunks=10,
    ),
    EntitySpec(
        "Paper recommender system",
        ExperienceKind.PROJECT,
        ExperienceSection.PROJECTS_HACKATHONS,
        0,
        0,
        0,
        topic="papers",
    ),
)

TOTAL_PENDING_CLAIMS = sum(spec.claims for spec in ENTITY_SPECS)  # 148 by construction

_VERBS = ("Built", "Automated", "Refactored", "Migrated", "Instrumented")
_MODULES = (
    "ingestion",
    "validation",
    "reporting",
    "export",
    "scheduling",
    "reconciliation",
    "alerting",
    "search",
)
_TOOLS = ("Pandas", "SQL", "Postgres", "Docker", "FastAPI", "Airflow", "Redis")


def _slug(name: str) -> str:
    return "-".join(name.lower().split())


def _base_ref(spec: EntitySpec) -> tuple[str, str]:
    """(source_type, base source_ref) for this entity's generated chunks."""
    if spec.kind is ExperienceKind.PROJECT:
        return SOURCE_GITHUB_README, (spec.aliases[0] if spec.aliases else _slug(spec.name))
    return SOURCE_DRIVE, f"doc-{_slug(spec.name)}"


def _action(spec: EntitySpec, i: int) -> tuple[str, tuple[str, ...]]:
    verb = _VERBS[i % len(_VERBS)]
    module = _MODULES[i % len(_MODULES)]
    tool = _TOOLS[i % len(_TOOLS)]
    text = f"{verb} the {spec.topic} {module} step in Python with {tool}"
    return text, ("Python", tool)


def _problem(spec: EntitySpec, i: int) -> tuple[str, CostDimension, Inefficiency]:
    if i < len(spec.bug_line_problems):
        # A one-line code bug: real at claim level, failing V3's problem-space bar.
        return spec.bug_line_problems[i], CostDimension.QUALITY, Inefficiency.NOT_WORKING
    module = _MODULES[i % len(_MODULES)]
    text = (
        f"The {spec.topic} team spent {4 + i} hours a week reconciling "
        f"{module} spreadsheets by hand"
    )
    return text, CostDimension.TIME, Inefficiency.MANUAL


def _result(spec: EntitySpec, i: int) -> tuple[str, ResultKind, dict[str, object]]:
    module = _MODULES[i % len(_MODULES)]
    if i % 2 == 0:
        metric = f"from {10 + i} hours to 2 hours"
        text = f"Reduced {spec.topic} {module} time {metric}"
        return text, ResultKind.QUANTIFIED, {"resolves": "time", "metric_text": metric}
    text = f"The {spec.topic} team no longer reconciles {module} exports by hand"
    return text, ResultKind.QUALITATIVE_EVIDENCED, {"resolves": "manual"}


@dataclass(frozen=True)
class _SpecialResult:
    """An override for one result slot (the duplicate-quote and conflict cases)."""

    quote: str
    source_type: str
    source_ref: str
    chunk_text: str


def _special_results(spec: EntitySpec) -> dict[int, _SpecialResult]:
    """Result-slot overrides wiring in the audited live pathologies."""
    if spec.name == "OneWorld":
        return {
            0: _SpecialResult(
                quote=SHARED_OUTCOME_QUOTE,
                source_type=SOURCE_GITHUB_README,
                source_ref=span_ref("cmorreale99/oneworld", 0, 96),
                chunk_text=(
                    "OneWorld — hackathon translation assistant. "
                    f"Placed {SHARED_OUTCOME_QUOTE} at the AnalyticsWorks hackathon."
                ),
            )
        }
    if spec.name == "cameron-morreale-portfolio":
        return {
            6: _SpecialResult(
                quote=SHARED_OUTCOME_QUOTE,
                source_type=SOURCE_DRIVE,
                source_ref=span_ref("doc-portfolio-site", 400, 512),
                chunk_text=(
                    f"Portfolio highlights: OneWorld hackathon entry — {SHARED_OUTCOME_QUOTE}."
                ),
            )
        }
    if spec.name == "MassDEP":
        return {
            5: _SpecialResult(
                quote=CONFLICT_QUOTE_V1,
                source_type=SOURCE_DRIVE,
                source_ref=span_ref("doc-massdep-resume-v1", 0, 90),
                chunk_text=f"Rebuilt the permit export checks, {CONFLICT_QUOTE_V1}.",
            ),
            6: _SpecialResult(
                quote=CONFLICT_QUOTE_V2,
                source_type=SOURCE_DRIVE,
                source_ref=span_ref("doc-massdep-resume-v2", 0, 110),
                chunk_text=f"Rebuilt the permit export checks, {CONFLICT_QUOTE_V2}.",
            ),
        }
    return {}


def _build_claim(
    spec: EntitySpec, i: int, source_type: str, base_ref: str
) -> tuple[DraftClaim, tuple[str, ...]]:
    """One pending claim (and its integrity flags) for slot ``i`` of this entity."""
    action_text, tools = _action(spec, i)
    has_problem = i < spec.with_problem
    has_result = i < spec.with_result
    special = _special_results(spec).get(i) if has_result else None

    problem_text: str | None = None
    cost: CostDimension | None = None
    inefficiency: Inefficiency | None = None
    if has_problem:
        problem_text, cost, inefficiency = _problem(spec, i)

    statements = [s for s in (problem_text, action_text) if s]
    result_text: str | None = None
    result_kind = ResultKind.MISSING
    result_metric: dict[str, object] | None = None
    if has_result and special is None:
        result_text, result_kind, result_metric = _result(spec, i)
        statements.append(result_text)
    chunk = EvidenceChunk(
        source_type=source_type,
        source_ref=span_ref(base_ref, i * 600, i * 600 + 400),
        chunk_text="\n".join(statements),
    )

    evidence = [ClaimEvidenceRef(chunk=chunk, field=ClaimField.ACTION)]
    if has_problem:
        evidence.append(ClaimEvidenceRef(chunk=chunk, field=ClaimField.PROBLEM))
    if has_result:
        if special is not None:
            result_text = special.quote
            result_kind = ResultKind.QUALITATIVE_EVIDENCED
            result_metric = {"resolves": "quality"}
            result_chunk = EvidenceChunk(
                source_type=special.source_type,
                source_ref=special.source_ref,
                chunk_text=special.chunk_text,
            )
            evidence.append(
                ClaimEvidenceRef(
                    chunk=result_chunk, field=ClaimField.RESULT, outcome_quote=special.quote
                )
            )
        else:
            evidence.append(
                ClaimEvidenceRef(chunk=chunk, field=ClaimField.RESULT, outcome_quote=result_text)
            )

    # Integrity flags mimicking the live distribution: a Result with no declared
    # Problem carries the coupling flag; every fifth claim declares a tool its text
    # never names. (Absence flags never persist — V3 Phase 0.)
    flags: list[str] = []
    if has_result and not has_problem:
        flags.append(f"result_problem_coupling: {COUPLING_FLAG}")
    if i % 5 == 4:
        tools = (*tools, "Kafka")
        flags.append("action_tool_not_in_text: declared tool 'Kafka' is not in the Action text")

    draft = DraftClaim(
        action_text=action_text,
        action_tools=tools,
        problem_text=problem_text,
        problem_cost_dimension=cost,
        problem_inefficiency=inefficiency,
        result_text=result_text,
        result_kind=result_kind,
        result_metric_json=result_metric,
        evidence=tuple(evidence),
    )
    return draft, tuple(flags)


def _storable(draft: DraftClaim, flags: tuple[str, ...]) -> StorableClaim:
    result_status = (
        ResultStatus.UNVERIFIED
        if flags or draft.result_kind is ResultKind.MISSING
        else ResultStatus.VERIFIED
    )
    return StorableClaim(
        draft=draft,
        status=ClaimStatus.PENDING_REVIEW,
        result_status=result_status,
        validation_flags=flags,
    )


@dataclass(frozen=True)
class LiveCorpus:
    """The built corpus: repository plus lookups the exit tests use."""

    user_id: str
    repository: InMemoryClaimRepository
    entities: dict[str, Experience] = field(default_factory=dict)  # by name
    approved_claim_id: int = 0
    rejected_claim_id: int = 0

    def entity(self, name: str) -> Experience:
        return self.entities[name]

    def claims_for(self, name: str) -> list[Claim]:
        experience = self.entities[name]
        return [
            c for c in self.repository.list_claims(self.user_id) if c.experience_id == experience.id
        ]


def _seed_reviewed_claims(
    repository: ClaimRepository, entities: dict[str, Experience]
) -> tuple[int, int]:
    """The one approved and one rejected claim the live database carries."""
    engine = entities["investment-decision-workflow-engine"]
    chunk = EvidenceChunk(
        source_type=SOURCE_GITHUB_COMMIT,
        source_ref="cmorreale99/investment-decision-workflow-engine@a1b2c3d",
        chunk_text=(
            "streamlit run executed dashboard.py as a top-level script and dropped state\n"
            "Reworked the screening dashboard entrypoint in Python with Streamlit\n"
            "Reduced screening dashboard reload time from 40 seconds to 4 seconds"
        ),
    )
    approved_quote = "Reduced screening dashboard reload time from 40 seconds to 4 seconds"
    approved_draft = DraftClaim(
        action_text="Reworked the screening dashboard entrypoint in Python with Streamlit",
        action_tools=("Python", "Streamlit"),
        # Live claim 373's shape: a one-line code-bug "Problem" naming a file —
        # approved at claim level, failing V3's problem-space bar.
        problem_text="streamlit run executed dashboard.py as a top-level script and dropped state",
        problem_cost_dimension=CostDimension.QUALITY,
        problem_inefficiency=Inefficiency.NOT_WORKING,
        result_text=approved_quote,
        result_kind=ResultKind.QUANTIFIED,
        result_metric_json={"resolves": "quality", "metric_text": "from 40 seconds to 4 seconds"},
        evidence=(
            ClaimEvidenceRef(chunk=chunk, field=ClaimField.PROBLEM),
            ClaimEvidenceRef(chunk=chunk, field=ClaimField.ACTION),
            ClaimEvidenceRef(chunk=chunk, field=ClaimField.RESULT, outcome_quote=approved_quote),
        ),
    )
    [approved] = repository.replace_unreviewed_claims(
        engine.user_id, engine.id, [_storable(approved_draft, ())]
    )
    repository.transition_claim(approved.id, ClaimStatus.APPROVED)

    wellington = entities["Wellington Management"]
    rejected_chunk = EvidenceChunk(
        source_type=SOURCE_DRIVE,
        source_ref=span_ref("doc-wellington-management", 9000, 9100),
        chunk_text="Attended the weekly holdings sync and took meeting notes",
    )
    rejected_draft = DraftClaim(
        action_text="Attended the weekly holdings sync and took meeting notes",
        action_tools=("Excel",),
        evidence=(ClaimEvidenceRef(chunk=rejected_chunk, field=ClaimField.ACTION),),
    )
    [rejected] = repository.replace_unreviewed_claims(
        wellington.user_id, wellington.id, [_storable(rejected_draft, ())]
    )
    repository.transition_claim(
        rejected.id, ClaimStatus.REJECTED, review_note="not career-relevant work"
    )
    return approved.id, rejected.id


def build_live_corpus(
    repository: InMemoryClaimRepository | None = None, user_id: str = LIVE_USER
) -> LiveCorpus:
    """Build the full live-shaped corpus into a repository (fresh one by default)."""
    repository = repository or InMemoryClaimRepository()

    entities: dict[str, Experience] = {}
    for spec in ENTITY_SPECS:
        seed = ExperienceSeed(
            name=spec.name, section=spec.section, kind=spec.kind, aliases=spec.aliases
        )
        proposed = repository.propose_experience(user_id, seed)
        entities[spec.name] = repository.set_experience_status(
            proposed.id, ExperienceStatus.CONFIRMED
        )

    approved_id, rejected_id = _seed_reviewed_claims(repository, entities)

    for spec in ENTITY_SPECS:
        experience = entities[spec.name]
        source_type, base_ref = _base_ref(spec)

        storables: list[StorableClaim] = []
        for i in range(spec.claims):
            draft, flags = _build_claim(spec, i, source_type, base_ref)
            # Chunks are assigned to the entity first, so the claims' citations land
            # on evidence the roster already scoped (upsert dedupes on the span ref).
            for ref in draft.evidence:
                stored = repository.upsert_evidence(user_id, ref.chunk)
                repository.assign_evidence(stored.id, experience.id)
            storables.append(_storable(draft, flags))
        if storables:
            repository.replace_unreviewed_claims(user_id, experience.id, storables)

        if spec.extra_chunks:
            commit_source = spec.aliases[0] if spec.aliases else _slug(spec.name)
            for i in range(spec.extra_chunks):
                stored = repository.upsert_evidence(
                    user_id,
                    EvidenceChunk(
                        source_type=SOURCE_GITHUB_COMMIT,
                        source_ref=f"{commit_source}@{i:07x}",
                        chunk_text=f"chore: iterate on the {spec.topic} pipeline, step {i}",
                    ),
                )
                repository.assign_evidence(stored.id, experience.id)

    return LiveCorpus(
        user_id=user_id,
        repository=repository,
        entities=entities,
        approved_claim_id=approved_id,
        rejected_claim_id=rejected_id,
    )
