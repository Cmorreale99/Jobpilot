"""Golden-corpus digest for the normalizer — version-bump enforcement (hardening H3).

``NORMALIZATION_VERSION`` was a manual discipline: a comment said "BUMP THIS on ANY
change" and nothing checked it, so a rule edit could ship silently and dangle every
stored ``#chars=`` span (PIPELINE_HARDENING_PLAN.md F4). This module makes the bump
mechanical: the committed digest (``tests/fixtures/normalization_corpus/digest.json``)
pins the normalizer's exact output over a corpus of pathological inputs, keyed by the
version that produced it. The paired test fails when either

* the output changed but the version did not (rule change without a bump), or
* the version changed but the digest was not re-blessed.

Re-bless after a deliberate change (bump ``NORMALIZATION_VERSION`` first)::

    python -m app.tools.normalization_digest --bless

Run without flags to print the current version + computed digest.
"""

from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path

from app.domain.text_normalization import NORMALIZATION_VERSION, normalize_source_text

CORPUS_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "normalization_corpus"
DIGEST_FILE = CORPUS_DIR / "digest.json"


def compute_corpus_digest(corpus_dir: Path = CORPUS_DIR) -> str:
    """Hash the normalizer's output over every corpus input, order-stable."""
    hasher = sha256()
    for path in sorted(corpus_dir.glob("*.txt")):
        hasher.update(path.name.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(normalize_source_text(path.read_text(encoding="utf-8")).encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def corpus_inputs(corpus_dir: Path = CORPUS_DIR) -> list[Path]:
    return sorted(corpus_dir.glob("*.txt"))


def read_blessed(digest_file: Path = DIGEST_FILE) -> dict[str, object]:
    blessed: dict[str, object] = json.loads(digest_file.read_text(encoding="utf-8"))
    return blessed


def bless(digest_file: Path = DIGEST_FILE) -> dict[str, object]:
    """Commit the current normalizer output as the blessed digest for this version."""
    blessed = {
        "normalization_version": NORMALIZATION_VERSION,
        "digest": compute_corpus_digest(digest_file.parent),
    }
    digest_file.write_text(json.dumps(blessed, indent=2) + "\n", encoding="utf-8")
    return blessed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bless",
        action="store_true",
        help="rewrite digest.json for the current NORMALIZATION_VERSION",
    )
    args = parser.parse_args()
    if args.bless:
        blessed = bless()
        print(f"blessed v{blessed['normalization_version']}: {blessed['digest']}")
    else:
        print(f"NORMALIZATION_VERSION={NORMALIZATION_VERSION}")
        print(f"computed digest: {compute_corpus_digest()}")


if __name__ == "__main__":
    main()
