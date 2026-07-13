from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from app.second_brain.domain.note import MemoryStatus, NoteRecord


class VaultIngestionService:
    """Ingest markdown notes from a local Obsidian-style vault."""

    def __init__(self, vault_path: str | Path) -> None:
        self.vault_path = Path(vault_path)

    def list_note_paths(self) -> list[Path]:
        if not self.vault_path.exists():
            return []
        return sorted(path for path in self.vault_path.rglob("*.md") if path.is_file())

    def ingest(self, paths: Iterable[str | Path] | None = None) -> list[NoteRecord]:
        selected_paths = (
            [Path(path) for path in paths] if paths is not None else self.list_note_paths()
        )
        return [NoteRecord.from_path(path) for path in selected_paths if path.exists()]

    def approve(self, note: NoteRecord) -> NoteRecord:
        note.status = MemoryStatus.APPROVED
        return note

    def reject(self, note: NoteRecord) -> NoteRecord:
        note.status = MemoryStatus.REJECTED
        return note
