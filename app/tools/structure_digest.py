"""Golden-corpus digest for the structurer — version-bump enforcement (hardening H4).

The twin of ``app/tools/normalization_digest.py``: persisted ``source_elements`` are a
derivation of raw text under ``STRUCTURER_VERSION``, so a scanning-rule change that
ships without a bump would silently disagree with every stored tree. The committed
digest (``tests/fixtures/normalization_corpus/structure_digest.json``) pins the
structurer's exact output — types, levels, spans, parents — over the same pathological
corpus, keyed by the version that produced it.

Re-bless after a deliberate change (bump ``STRUCTURER_VERSION`` first)::

    python -m app.tools.structure_digest --bless
"""

from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path

from app.domain.source_structure import STRUCTURER_VERSION, structure_source_text

CORPUS_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "normalization_corpus"
DIGEST_FILE = CORPUS_DIR / "structure_digest.json"


def compute_structure_digest(corpus_dir: Path = CORPUS_DIR) -> str:
    """Hash the structurer's full output over every corpus input, order-stable."""
    hasher = sha256()
    for path in sorted(corpus_dir.glob("*.txt")):
        hasher.update(path.name.encode("utf-8"))
        hasher.update(b"\x00")
        for element in structure_source_text(path.read_text(encoding="utf-8")):
            line = (
                f"{element.sequence_index}|{element.element_type}|{element.level}"
                f"|{element.raw_start}-{element.raw_end}|{element.parent_index}"
            )
            hasher.update(line.encode("utf-8"))
            hasher.update(b"\x00")
    return hasher.hexdigest()


def read_blessed(digest_file: Path = DIGEST_FILE) -> dict[str, object]:
    blessed: dict[str, object] = json.loads(digest_file.read_text(encoding="utf-8"))
    return blessed


def bless(digest_file: Path = DIGEST_FILE) -> dict[str, object]:
    """Commit the current structurer output as the blessed digest for this version."""
    blessed = {
        "structurer_version": STRUCTURER_VERSION,
        "digest": compute_structure_digest(digest_file.parent),
    }
    digest_file.write_text(json.dumps(blessed, indent=2) + "\n", encoding="utf-8")
    return blessed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bless",
        action="store_true",
        help="rewrite structure_digest.json for the current STRUCTURER_VERSION",
    )
    args = parser.parse_args()
    if args.bless:
        blessed = bless()
        print(f"blessed v{blessed['structurer_version']}: {blessed['digest']}")
    else:
        print(f"STRUCTURER_VERSION={STRUCTURER_VERSION}")
        print(f"computed digest: {compute_structure_digest()}")


if __name__ == "__main__":
    main()
