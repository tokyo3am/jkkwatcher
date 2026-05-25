from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .models import Property


@dataclass(frozen=True, slots=True)
class Diff:
    added: list[Property] = field(default_factory=list)
    removed: list[Property] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.added and not self.removed


def compute(previous: list[Property], current: list[Property]) -> Diff:
    prev_map = {p.key: p for p in previous}
    curr_map = {p.key: p for p in current}

    added = [curr_map[k] for k in curr_map.keys() - prev_map.keys()]
    removed = [prev_map[k] for k in prev_map.keys() - curr_map.keys()]
    return Diff(added=added, removed=removed)


def load_state(path: Path) -> list[Property]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Property(**item) for item in data]


def save_state(path: Path, properties: list[Property]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([p.to_dict() for p in properties], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
