"""Suumo 物件の最寄り駅に乗り入れる「全路線」を補完するための駅→路線辞書。

Suumo の access 欄は各駅につき一部の路線しか出さない (下高井戸=東急世田谷線
だけで京王線が落ちる等)。ekidata.jp 由来の同梱辞書 (data/station_lines.json、
scripts/gen_station_lines.py で生成) を、Suumo が提示する (駅名, 路線) を
アンカーに引き、乗り入れ全路線へ広げる。

同名異駅 (本町・春日 等) はアンカー路線で一意化し、特定できない/曖昧なら
補完せず Suumo 提示路線のまま (捏造しない・安全フォールバック)。
"""
from __future__ import annotations

import json
from pathlib import Path

from .line_emoji import _normalize as _normalize_jr
from .line_emoji import route_emoji

_DATA_PATH = Path(__file__).parent / "data" / "station_lines.json"

# 駅名 (末尾「駅」を除いた表記) -> 同名駅候補のリスト。
# 候補 = {"lines": [路線名...], "pref_cd": int, "station_g_cd": int, "lat", "lon"}
Candidate = dict[str, object]


def _normalize_line(line: str) -> str:
    """路線名の表記ゆらぎを吸収して比較するための正規化キー。

    全角ＪＲ→半角JR (line_emoji と共通) に加え、空白・中点・スラッシュを
    除去する ("中央・総武線" と "中央／総武線" 等を同一視)。
    """
    s = _normalize_jr(line)
    for ch in (" ", "　", "・", "／", "/"):
        s = s.replace(ch, "")
    return s


def _lines_match(a: str, b: str) -> bool:
    """2 つの路線名が同一路線を指すか。正規化一致 or 絵文字解決一致。

    絵文字解決一致 = route_emoji(a) と route_emoji(b) が共に非 None で同値。
    line_emoji のエイリアス表 (京王線/京王新線→keio 等) が表記差を吸収する。
    """
    if _normalize_line(a) == _normalize_line(b):
        return True
    ea = route_emoji(a)
    eb = route_emoji(b)
    return ea is not None and ea == eb


def _cand_lines(candidate: Candidate) -> list[str]:
    """候補の lines を list[str] として取り出す (型と欠損に頑健)。"""
    lines = candidate.get("lines")
    if isinstance(lines, list):
        return [str(x) for x in lines]
    return []


def _any_line_matches(suumo_lines: list[str], cand_lines: list[str]) -> bool:
    """Suumo 提示路線のいずれかが候補路線のいずれかと一致するか。"""
    return any(_lines_match(s, c) for s in suumo_lines for c in cand_lines)


class StationLineIndex:
    """駅名 → 乗り入れ全路線。Suumo のアンカー路線で同名駅を一意化する。"""

    def __init__(self, stations: dict[str, list[Candidate]]) -> None:
        self._stations = stations

    @classmethod
    def load(cls, path: Path = _DATA_PATH) -> StationLineIndex:
        """同梱 JSON を読む。不在・破損時は空 index に縮退 (補完を諦め通知は壊さない)。"""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            stations = data.get("stations", {})
        except (OSError, ValueError):
            stations = {}
        return cls(stations if isinstance(stations, dict) else {})

    def identify(
        self,
        station: str,
        anchor_lines: list[str],
        *,
        pref_cd: int | None = None,
    ) -> list[str] | None:
        """(駅名, Suumo 提示路線) から候補を一意化し乗り入れ全路線を返す。

        引けない / アンカー不一致 / 曖昧なら None (補完せず現状維持)。
        """
        key = station[:-1] if station.endswith("駅") else station
        candidates = self._stations.get(key)
        if not candidates:
            return None
        matched = [
            c for c in candidates if _any_line_matches(anchor_lines, _cand_lines(c))
        ]
        if len(matched) == 1:
            return _cand_lines(matched[0])
        if not matched:
            return None
        # 同名駅が複数あり双方にアンカーが乗り入れる稀ケース: pref で一意化、
        # 無理なら諦める (捏造しない)。
        if pref_cd is not None:
            by_pref = [c for c in matched if c.get("pref_cd") == pref_cd]
            if len(by_pref) == 1:
                return _cand_lines(by_pref[0])
        return None

    def complete(
        self,
        suumo_lines: list[str],
        station: str,
        *,
        pref_cd: int | None = None,
    ) -> list[str]:
        """suumo_lines に辞書の差分路線をマージ。Suumo 提示順を保ち末尾に追加。

        辞書を引けなければ suumo_lines をそのまま返す (現状維持)。
        """
        full = self.identify(station, suumo_lines, pref_cd=pref_cd)
        if full is None:
            return suumo_lines
        seen = {_normalize_line(x) for x in suumo_lines}
        merged = list(suumo_lines)
        for line in full:
            norm = _normalize_line(line)
            if norm not in seen:
                merged.append(line)
                seen.add(norm)
        return merged


_default_index: StationLineIndex | None = None


def default_index() -> StationLineIndex:
    """同梱辞書を一度だけロードして返す (遅延ロード)。"""
    global _default_index
    if _default_index is None:
        _default_index = StationLineIndex.load()
    return _default_index
