from pathlib import Path

from app.second_brain.domain.note import MemoryStatus, NoteRecord
from app.second_brain.services.vault_ingestion import VaultIngestionService


def test_ingest_markdown_notes_from_vault(tmp_path: Path) -> None:
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    note_path = vault_dir / "hello.md"
    note_path.write_text("# Hello\n\nThis is a test note.", encoding="utf-8")

    service = VaultIngestionService(vault_dir)
    notes = service.ingest([note_path])

    assert len(notes) == 1
    assert isinstance(notes[0], NoteRecord)
    assert notes[0].title == "Hello"
    assert "This is a test note." in notes[0].content


def test_approval_state_changes(tmp_path: Path) -> None:
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    note_path = vault_dir / "note.md"
    note_path.write_text("# Note", encoding="utf-8")

    service = VaultIngestionService(vault_dir)
    note = service.ingest([note_path])[0]

    service.approve(note)
    assert note.status is MemoryStatus.APPROVED

    service.reject(note)
    assert note.status is MemoryStatus.REJECTED
