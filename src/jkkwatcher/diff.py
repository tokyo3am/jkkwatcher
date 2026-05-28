from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Generic, TypeVar

from .models import PropertyLike, Property, SuumoProperty, UrProperty


P = TypeVar("P", bound=PropertyLike)


@dataclass(frozen=True, slots=True)
class Diff(Generic[P]):
    added: list[P] = field(default_factory=list)
    removed: list[P] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.added and not self.removed


def compute(previous: list[P], current: list[P]) -> Diff[P]:
    prev_map = {p.key: p for p in previous}
    curr_map = {p.key: p for p in current}

    added = [curr_map[k] for k in curr_map.keys() - prev_map.keys()]
    removed = [prev_map[k] for k in prev_map.keys() - curr_map.keys()]
    return Diff(added=added, removed=removed)


def load_state(path: Path, factory: Callable[..., P]) -> list[P]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [factory(**item) for item in data]


def save_state(path: Path, properties: list[P]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([p.to_dict() for p in properties], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# 互換関数: 既存の呼び出し (JKK Property を仮定) を壊さないために用意。
def load_jkk_state(path: Path) -> list[Property]:
    return load_state(path, Property)


def load_ur_state(path: Path) -> list[UrProperty]:
    return load_state(path, UrProperty)


def load_suumo_state(path: Path) -> list[SuumoProperty]:
    return load_state(path, SuumoProperty)
