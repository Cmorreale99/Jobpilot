from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class MemoryStatus(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


@dataclass(slots=True)
class NoteRecord:
    id: str
    source_path: str
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    frontmatter: dict[str, Any] = field(default_factory=dict)
    status: MemoryStatus = MemoryStatus.DRAFT
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_path(cls, path: str | Path, *, content: str | None = None) -> NoteRecord:
        path_obj = Path(path)
        raw_content = content if content is not None else path_obj.read_text(encoding="utf-8")
        title = path_obj.stem.replace("_", " ").replace("-", " ").title()
        return cls(
            id=f"note:{path_obj.stem}",
            source_path=str(path_obj),
            title=title,
            content=raw_content,
            tags=[],
            frontmatter={},
        )
