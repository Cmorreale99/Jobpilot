"""Repository-document identity rules (pure): which tree paths are career documents.

Encodes the MASTER CV REPAIR spec's GitHub universe (§4.1, §6.2, §22.1 resolution):

* Every README — root **and nested** — and every ``CLAUDE.md`` is admitted career
  documentation, NO EXCEPTIONS.
* All other Markdown (project docs, architecture/design docs) is admitted.
* Source code, tests, notebooks, and everything else remain in the enumerated
  universe with an explicit *awaiting user decision* disposition (§22.1 leaves their
  admission unresolved) — they never silently disappear, and they are never silently
  ingested either.

Shared by the gather service (admission + accounting) and the roster proposer
(nested READMEs propose child projects). Pure logic: no I/O, no service imports.
"""

from __future__ import annotations

import re

# Root README variants (case-insensitive basenames) accepted as "the" README.
_README_BASENAMES = frozenset(
    {"readme", "readme.md", "readme.markdown", "readme.rst", "readme.txt"}
)
_CLAUDE_BASENAME = "claude.md"
_MARKDOWN_SUFFIXES = (".md", ".markdown")


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1].strip().lower()


def is_readme_path(path: str) -> bool:
    """True when ``path``'s basename is a README variant (any depth)."""
    return _basename(path) in _README_BASENAMES


def is_root_readme(path: str) -> bool:
    """True for a README variant sitting at the repository root."""
    return "/" not in path.strip("/") and is_readme_path(path)


def is_claude_md(path: str) -> bool:
    """True when ``path`` is a CLAUDE.md (any depth)."""
    return _basename(path) == _CLAUDE_BASENAME


# Directory segments that hold test data, agent tooling, or vendored content — not
# the user's career documentation. Live finding (2026-07-12): admitting all Markdown
# pulled `tests/fixtures/**` into the corpus, whose files describe FICTIONAL careers
# (fabricated projects and metrics) — ingesting them risks invented evidence
# (§5.7.2). These paths stay enumerated with an awaiting-user-decision disposition;
# the user's §22.1 decision can admit them explicitly.
_NON_CAREER_SEGMENTS = frozenset(
    {
        "tests",
        "test",
        "fixtures",
        ".claude",
        ".github",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "templates",
    }
)


def _in_non_career_directory(path: str) -> bool:
    segments = [p.strip().lower() for p in path.split("/")[:-1]]
    return any(segment in _NON_CAREER_SEGMENTS for segment in segments)


def repo_doc_admission_reason(path: str) -> str | None:
    """Why this tree path is NOT admitted as a career document (``None`` = admitted).

    Admitted: READMEs (root + nested), CLAUDE.md, and all Markdown — except files
    under test/tooling/vendored directories, whose content is test data (often a
    fictional career), not the user's documentation. Everything not admitted is
    enumerated but awaits the user's §22.1 decision; nothing silently disappears.
    """
    if _in_non_career_directory(path):
        return (
            "in a test/tooling/vendored directory (test fixtures often describe a "
            "FICTIONAL career) — awaiting the user's §22.1 admission decision"
        )
    if is_readme_path(path) or is_claude_md(path):
        return None
    if _basename(path).endswith(_MARKDOWN_SUFFIXES):
        return None
    return (
        "not in the admitted GitHub document universe (source/tests/notebooks and "
        "other non-Markdown files await the user's §22.1 admission decision)"
    )


_MULTI_ENTITY_BASENAMES = re.compile(r"(^|[^a-z])(resume|cv)([^a-z]|$)", re.IGNORECASE)


def is_multi_entity_doc(path: str) -> bool:
    """True for docs that describe MANY entities (resumes/CVs checked into a repo).

    §6.1: one document may contain multiple projects or roles. A resume inside a
    single-project repository must never inherit the repo's entity boundary — its
    sections are owned per section, like any multi-entity document. (Live finding
    2026-07-12: `Jobpilot/resume.md` would otherwise force Cooper/OneWorld content
    under the JobPilot entity.)
    """
    stem = _basename(path).rsplit(".", 1)[0]
    return bool(_MULTI_ENTITY_BASENAMES.search(stem))


def repo_doc_title(repo_name: str, path: str) -> str:
    """Human title for a repo document: nested READMEs take their directory's name."""
    parts = [p for p in path.split("/") if p]
    if is_readme_path(path) and len(parts) > 1:
        return parts[-2]
    return f"{repo_name}/{path}"


def nested_readme_project_name(path: str) -> str | None:
    """The child-project name a nested README declares (its directory), or ``None``."""
    parts = [p for p in path.split("/") if p]
    if is_readme_path(path) and len(parts) > 1:
        return parts[-2]
    return None


__all__ = [
    "is_claude_md",
    "is_multi_entity_doc",
    "is_readme_path",
    "is_root_readme",
    "nested_readme_project_name",
    "repo_doc_admission_reason",
    "repo_doc_title",
]
