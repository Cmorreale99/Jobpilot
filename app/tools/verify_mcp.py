"""Live MCP verification — ``python -m app.tools.verify_mcp [github|drive|all]``.

Closes the M6 loop: connects to the *real* MCP servers with the current settings and
exercises exactly what ingestion needs (connect → advertised tools → list → read →
metadata), printing a PASS/FAIL report. This is the repeatable answer to PLAN.md's
"live verification" items: auth contract, tool names, and payload format all fail
loudly here with the server's actual response, instead of failing mid-pipeline.

Read-only against your own data. Uses the same factories and dev-credential fallbacks
as the pipeline (``GITHUB_MCP_ACCESS_TOKEN``; ``GDRIVE_MCP_USER_EMAIL`` +
``GDRIVE_MCP_ACCESS_TOKEN``), so a token in ``.env`` is enough — no OAuth flow needed
for a first check.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import traceback

from app.config import Settings, get_settings
from app.integrations.drive_factory import create_drive_client
from app.integrations.github_factory import create_github_client

logger = logging.getLogger(__name__)

_TRUNCATE = 200


def _snippet(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= _TRUNCATE else flat[:_TRUNCATE] + "…"


def _fail(check: str, exc: Exception) -> None:
    print(f"  FAIL {check}: {type(exc).__name__}: {exc}")


async def verify_github(settings: Settings) -> bool:
    print("== GitHub MCP ==")
    print(
        f"  transport={settings.github_mcp_transport!r} "
        f"server={settings.github_mcp_server!r} command={settings.github_mcp_command!r}"
    )
    if not settings.github_mcp_enabled:
        print("  SKIP: GITHUB_MCP_ENABLED is false.")
        return False
    try:
        client = create_github_client(settings)
    except Exception as exc:  # noqa: BLE001 - report any construction failure
        _fail("configuration", exc)
        return False

    try:
        repos = await client.list_candidate_repos(settings.github_username)
        print(f"  PASS connect + list_candidate_repos: {len(repos)} repo(s)")
        for repo in repos[:5]:
            print(f"       - {repo.repo_ref} ({repo.primary_language or '?'})")
    except Exception as exc:  # noqa: BLE001
        _fail("connect/list (auth contract or tool names)", exc)
        traceback.print_exc(limit=2)
        return False

    if not repos:
        print("  WARN no repositories visible; read/metadata checks skipped.")
        return True
    ref = repos[0].repo_ref
    ok = True
    try:
        document = await client.read_repo(ref)
        print(f"  PASS read_repo({ref}): {_snippet(document.text) or '(empty README)'}")
    except Exception as exc:  # noqa: BLE001
        _fail(f"read_repo({ref}) (payload format)", exc)
        ok = False
    try:
        meta = await client.get_repo_metadata(ref)
        print(
            f"  PASS get_repo_metadata({ref}): stars={meta.stars} "
            f"commits={meta.commit_count} languages={list(meta.languages) or '?'}"
        )
    except Exception as exc:  # noqa: BLE001
        _fail(f"get_repo_metadata({ref})", exc)
        ok = False
    return ok


async def verify_drive(settings: Settings) -> bool:
    print("== Google Drive MCP ==")
    print(
        f"  transport={settings.gdrive_mcp_transport!r} "
        f"server={settings.gdrive_mcp_server!r} command={settings.gdrive_mcp_command!r}"
    )
    if not settings.gdrive_mcp_enabled:
        print("  SKIP: GDRIVE_MCP_ENABLED is false.")
        return False
    try:
        client = create_drive_client(settings)
    except Exception as exc:  # noqa: BLE001
        _fail("configuration", exc)
        return False

    try:
        sources = await client.list_candidate_sources(settings.pipeline_user_id)
        print(f"  PASS connect + list_candidate_sources: {len(sources)} candidate(s)")
        for source in sources[:5]:
            print(f"       - {source.title} ({source.mime_type})")
    except Exception as exc:  # noqa: BLE001
        _fail("connect/list (auth contract or tool names)", exc)
        traceback.print_exc(limit=2)
        return False

    if not sources:
        print("  WARN no candidates in scope (check GDRIVE_SOURCE_FOLDER_ID); read skipped.")
        return True
    ref = sources[0].source_ref
    try:
        document = await client.read_source(ref)
        print(f"  PASS read_source({sources[0].title!r}): {_snippet(document.text)}")
        return True
    except Exception as exc:  # noqa: BLE001
        _fail(f"read_source({ref}) (payload format — see _extract_payload)", exc)
        return False


async def _run(target: str, settings: Settings) -> int:
    results: list[bool] = []
    if target in ("github", "all"):
        results.append(await verify_github(settings))
    if target in ("drive", "all"):
        results.append(await verify_drive(settings))
    if all(results):
        print("\nAll requested verifications passed.")
        return 0
    print("\nSome verifications failed or were skipped — see above.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify live MCP integrations (read-only)")
    parser.add_argument("target", choices=["github", "drive", "all"], nargs="?", default="all")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(_run(args.target, get_settings()))


if __name__ == "__main__":
    sys.exit(main())
