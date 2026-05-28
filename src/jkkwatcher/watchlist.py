from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Literal, Protocol

Source = Literal["jkk", "ur", "suumo"]
_VALID_SOURCES: frozenset[str] = frozenset(("jkk", "ur", "suumo"))

ENV_VAR = "JKKWATCHER_CONFIG_JSON"


class _Named(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def building_key(self) -> str: ...


@dataclass(frozen=True, slots=True)
class WatchlistEntry:
    """1 件のマッチ条件。building_key と name_contains はどちらか一方のみ指定。

    - building_key: 建物単位の完全一致 (JKK residence_code 等)
    - name_contains: 物件名の部分一致 (大文字小文字無視)
    """

    source: Source
    building_key: str | None = None
    name_contains: str | None = None

    def __post_init__(self) -> None:
        has_bkey = self.building_key is not None
        has_name = self.name_contains is not None
        if has_bkey and has_name:
            raise ValueError(
                "WatchlistEntry: specify only one of building_key or name_contains"
            )
        if not has_bkey and not has_name:
            raise ValueError(
                "WatchlistEntry: must specify either building_key or name_contains"
            )

    def matches(self, prop: _Named) -> bool:
        if self.building_key is not None:
            return prop.building_key == self.building_key
        # __post_init__ により、building_key が無ければ name_contains は必ず存在する。
        assert self.name_contains is not None
        return self.name_contains.casefold() in prop.name.casefold()


@dataclass(frozen=True, slots=True)
class NotifyConfig:
    """Slack 通知の挙動を制御する設定。

    - mention_on_added: 1 件以上の新着があれば一律 @channel を付ける
    - mention_on_watch_hit: watchlist 一致の新着があれば @channel を付ける
    - watchlist: ソース別のマッチ条件 (建物 key 完全一致 / 物件名部分一致)
    """

    mention_on_added: bool = False
    mention_on_watch_hit: bool = True
    watchlist: tuple[WatchlistEntry, ...] = ()

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> NotifyConfig:
        environ = env if env is not None else os.environ
        raw = (environ.get(ENV_VAR) or "").strip()
        if not raw:
            return cls()
        return cls.from_json(raw)

    @classmethod
    def from_json(cls, raw: str) -> NotifyConfig:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"{ENV_VAR} must be a JSON object")

        entries_raw = data.get("watchlist", [])
        if not isinstance(entries_raw, list):
            raise ValueError("watchlist must be a list")

        entries: list[WatchlistEntry] = []
        for i, item in enumerate(entries_raw):
            if not isinstance(item, dict):
                raise ValueError(f"watchlist[{i}] must be an object")
            source = item.get("source")
            if source not in _VALID_SOURCES:
                raise ValueError(
                    f"watchlist[{i}].source must be one of "
                    f"{sorted(_VALID_SOURCES)}: got {source!r}"
                )

            # 空文字は省略扱い (Variables 編集ミス対策)
            building_key = item.get("building_key") or None
            name_contains = item.get("name_contains") or None

            if building_key is not None and not isinstance(building_key, str):
                raise ValueError(
                    f"watchlist[{i}].building_key must be a string"
                )
            if name_contains is not None and not isinstance(name_contains, str):
                raise ValueError(
                    f"watchlist[{i}].name_contains must be a string"
                )

            try:
                entries.append(
                    WatchlistEntry(
                        source=source,
                        building_key=building_key,
                        name_contains=name_contains,
                    )
                )
            except ValueError as e:
                raise ValueError(f"watchlist[{i}]: {e}") from e

        return cls(
            mention_on_added=bool(data.get("mention_on_added", False)),
            mention_on_watch_hit=bool(data.get("mention_on_watch_hit", True)),
            watchlist=tuple(entries),
        )

    def is_hit(self, source: Source, prop: _Named) -> bool:
        for entry in self.watchlist:
            if entry.source != source:
                continue
            if entry.matches(prop):
                return True
        return False
