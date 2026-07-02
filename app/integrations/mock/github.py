"""Fixture-backed :class:`GitHubClient`.

The default GitHub implementation in dev and tests. Reads a local ``manifest.json`` plus
README files under a fixtures directory, so Master CV ingestion from GitHub runs
end-to-end with **zero token, zero MCP, and zero network**.

The mock returns *raw* candidates (every repo in the manifest). Repo policy — owner
scope and fork/private exclusion — is applied by the service layer, matching how the
real MCP client behaves.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.integrations.base import (
    GitHubDocument,
    GitHubRepo,
    GitHubRepoMetadata,
)

_MANIFEST_NAME = "manifest.json"


def _parse_time(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _opt_str(value: object) -> str | None:
    return str(value) if value else None


def _int(value: object, default: int = 0) -> int:
    return int(value) if isinstance(value, (int, str)) else default


def _opt_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, str)) else None


class MockGitHubClient:
    """Reads repository fixtures from a local directory."""

    def __init__(self, fixtures_dir: str | Path) -> None:
        self._fixtures_dir = Path(fixtures_dir)
        self._manifest = self._load_manifest()

    def _load_manifest(self) -> dict[str, dict[str, object]]:
        manifest_path = self._fixtures_dir / _MANIFEST_NAME
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"Mock GitHub manifest not found at {manifest_path}. "
                "Set GITHUB_MOCK_FIXTURES_DIR or add tests/fixtures/github/manifest.json."
            )
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        return {str(entry["repo_ref"]): entry for entry in raw["repos"]}

    def _entry(self, repo_ref: str) -> dict[str, object]:
        try:
            return self._manifest[repo_ref]
        except KeyError as exc:
            raise KeyError(f"Unknown mock GitHub repo_ref: {repo_ref!r}") from exc

    def _to_repo(self, entry: dict[str, object]) -> GitHubRepo:
        return GitHubRepo(
            repo_ref=str(entry["repo_ref"]),
            name=str(entry["name"]),
            owner=str(entry["owner"]),
            description=_opt_str(entry.get("description")),
            primary_language=_opt_str(entry.get("primary_language")),
            is_fork=bool(entry.get("is_fork", False)),
            is_private=bool(entry.get("is_private", False)),
            stars=_int(entry.get("stars")),
            pushed_at=_parse_time(entry.get("pushed_at")),
        )

    async def list_candidate_repos(self, user_id: str) -> list[GitHubRepo]:
        return [self._to_repo(entry) for entry in self._manifest.values()]

    async def read_repo(self, repo_ref: str) -> GitHubDocument:
        entry = self._entry(repo_ref)
        readme_path = self._fixtures_dir / str(entry["readme"])
        text = readme_path.read_text(encoding="utf-8")
        return GitHubDocument(
            repo_ref=repo_ref,
            title=str(entry["name"]),
            text=text,
            primary_language=_opt_str(entry.get("primary_language")),
            pushed_at=_parse_time(entry.get("pushed_at")),
        )

    async def get_repo_metadata(self, repo_ref: str) -> GitHubRepoMetadata:
        entry = self._entry(repo_ref)
        languages = entry.get("languages", [])
        return GitHubRepoMetadata(
            repo_ref=repo_ref,
            name=str(entry["name"]),
            owner=str(entry["owner"]),
            primary_language=_opt_str(entry.get("primary_language")),
            languages=tuple(str(lang) for lang in languages) if isinstance(languages, list) else (),
            stars=_int(entry.get("stars")),
            forks=_int(entry.get("forks")),
            commit_count=_opt_int(entry.get("commit_count")),
            pushed_at=_parse_time(entry.get("pushed_at")),
        )

    async def list_changed_repos(self, user_id: str, since: datetime) -> list[GitHubRepo]:
        repos = await self.list_candidate_repos(user_id)
        return [r for r in repos if r.pushed_at is not None and r.pushed_at > since]
