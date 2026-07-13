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


def repo_doc_admission_reason(path: str) -> str | None:
    """Why this tree path is NOT admitted as a career document (``None`` = admitted).

    Admitted: READMEs (root + nested), CLAUDE.md, and all Markdown. Everything else
    is enumerated but awaits the user's §22.1 decision.
    """
    if is_readme_path(path) or is_claude_md(path):
        return None
    if _basename(path).endswith(_MARKDOWN_SUFFIXES):
        return None
    return (
        "not in the admitted GitHub document universe (source/tests/notebooks and "
        "other non-Markdown files await the user's §22.1 admission decision)"
    )


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
    "is_readme_path",
    "is_root_readme",
    "nested_readme_project_name",
    "repo_doc_admission_reason",
    "repo_doc_title",
]
