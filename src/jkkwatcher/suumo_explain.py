"""Suumo 検索 URL を人間可読に解釈する。

方針: オフライン辞書 (`data/suumo_codes.json`) を基本に解釈し、未知のコードが
あればオンライン (SUUMO の SEO 沿線ページ) で解決して辞書へ書き戻す自己成長型。
辞書は次回以降オフラインで解決できるよう蓄積される。
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from .suumo_scraper import USER_AGENT

_DATA_PATH = Path(__file__).parent / "data" / "suumo_codes.json"

# rn (沿線) / ek (駅) は都道府県 (ta) で番号体系が変わるため ta 別ネスト。
_TA_NESTED_SECTIONS = frozenset({"rn", "ek"})

# code-dict パラメータ: (URL パラメータ名, 辞書セクション, ゼロ詰め桁数 or None)
_SINGLE_CODE_SPECS = (
    ("ar", "ar", None),
    ("bs", "bs", None),
    ("ta", "ta", None),
    ("po1", "po1", None),
    ("ekInput", "ek", 5),
)
_MULTI_CODE_SPECS = (
    ("ts", "ts", None),
    ("rn", "rn", None),
    ("tc", "tc", None),
)
_CODE_DICT_SPECS = _SINGLE_CODE_SPECS + _MULTI_CODE_SPECS

# オンラインで解決できるセクション (SEO 沿線ページ由来)。
_ONLINE_SECTIONS = frozenset({"tc", "rn", "ek"})

# 純システム/空パラメータ: 表示しない。
_IGNORE_PARAMS = frozenset({"po2", "fw", "fw2", "sngz", "kskbn"})

# param ↔ ラベルの帰属が未確定なもの。捏造せず best-effort で別枠に出す。
_STRUCTURE_PARAMS = ("shkr1", "shkr2", "shkr3", "shkr4")  # 建物構造 (鉄筋系?)
_OPAQUE_PARAMS = ("smk", "co")  # 意味未確定。param=value で出す。

# SEO ページ
_SEO_BASE = "https://suumo.jp/chintai"
_PREF_SLUG = {"13": "tokyo"}  # ta → SEO URL の {pref} スラッグ
_MAX_LINE_FETCHES = 100  # オンライン rn/ek 解決時の沿線ページ取得上限 (暴走防止)

_TC_RE = re.compile(r'name="tc" value="(\d+)"[^>]*id="[^"]+"[^>]*><label[^>]*>([^<]+)')
_EK_RE = re.compile(r'/ek_(\d{5})/\?rn=\d+"[^>]*>([^<]+)</a>')
_RN_RE = re.compile(r'(?:name="rn"\s+value="|[?&]rn=)(\d{4})')


class SuumoExplainError(RuntimeError):
    pass


# ---------- データモデル ----------


@dataclass(frozen=True, slots=True)
class ResolvedCode:
    param: str
    code: str
    label: str | None
    resolved_online: bool = False

    @property
    def display(self) -> str:
        if self.label is None:
            return f"{self.param}={self.code} (未解決)"
        return self.label


@dataclass(frozen=True, slots=True)
class SuumoSearchExplanation:
    url: str
    # スカラー (整形済み文字列。指定なしは None)
    rent: str | None = None
    floor_area: str | None = None
    walk_minutes: str | None = None
    commute: str | None = None
    building_age: str | None = None
    # code-dict 単一
    region: ResolvedCode | None = None  # ar
    property_kind: ResolvedCode | None = None  # bs
    prefecture: ResolvedCode | None = None  # ta
    sort_order: ResolvedCode | None = None  # po1
    station: ResolvedCode | None = None  # ekInput
    # code-dict 複数
    building_types: list[ResolvedCode] = field(default_factory=list)  # ts
    lines: list[ResolvedCode] = field(default_factory=list)  # rn
    features: list[ResolvedCode] = field(default_factory=list)  # tc
    # 不確実 / 未解決 / オンライン追記分
    uncertain: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    newly_resolved: list[ResolvedCode] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- スカラー整形 ----------


def _man_yen(v: str | None) -> str | None:
    """'20.0'→'20万円'。'0.0'/None/空→None。数値でなければ None。"""
    if not v:
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    if f == 0.0:
        return None
    return f"{f:g}万円"


def _fmt_rent(cb: str | None, ct: str | None) -> str | None:
    low, high = _man_yen(cb), _man_yen(ct)
    if low and high:
        return f"{low}〜{high}"
    if high:
        return f"〜{high}"
    if low:
        return f"{low}〜"
    return None


def _fmt_floor_area(mb: str | None, mt: str | None) -> str | None:
    def m2(v: str | None) -> str | None:
        if not v:
            return None
        try:
            return f"{float(v):g}m²"
        except ValueError:
            return None

    low, high = m2(mb), m2(mt)
    if low and high:
        return f"{low}〜{high}"
    if low:
        return f"{low}以上"
    if high:
        return f"{high}以下"
    return None


def _fmt_walk(et: str | None) -> str | None:
    if not et:
        return None
    try:
        return f"徒歩{int(et)}分以内"
    except ValueError:
        return None


def _fmt_commute(tj: str | None, nk: str | None) -> str | None:
    parts: list[str] = []
    if tj:
        try:
            parts.append(f"{int(tj)}分以内")
        except ValueError:
            pass
    if nk is not None:
        # nk: 乗換回数の上限。0 = 乗換なし。
        try:
            n = int(nk)
        except (ValueError, TypeError):
            n = None
        if n == 0:
            parts.append("乗換なし")
        elif n is not None:
            parts.append(f"乗換{n}回以内")
    return " / ".join(parts) if parts else None


def _fmt_building_age(cn: str | None) -> str | None:
    """築年数上限。9999999 (= 指定なし) は None。"""
    if not cn:
        return None
    try:
        years = int(cn)
    except ValueError:
        return None
    if years >= 9999999:
        return None
    return f"築{years}年以内"


# ---------- 辞書 I/O ----------


def load_codes(path: Path = _DATA_PATH) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_codes(codes: dict, path: Path = _DATA_PATH) -> bool:
    """atomic write。成功で True。書込不可は stderr に警告して False (落とさない)。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(codes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp, path)
        return True
    except OSError as e:
        print(f"[warn] 辞書を保存できませんでした ({path}): {e}", file=sys.stderr)
        return False


def _lookup(codes: dict, section: str, code: str, ta: str | None) -> str | None:
    sect = codes.get(section, {})
    if section in _TA_NESTED_SECTIONS:
        if ta is None:
            return None
        return sect.get(ta, {}).get(code)
    return sect.get(code)


def _merge_into_codes(codes: dict, resolved: dict, ta: str | None) -> None:
    for section, payload in resolved.items():
        if section in _TA_NESTED_SECTIONS:
            if ta is None:
                continue
            codes.setdefault(section, {}).setdefault(ta, {}).update(payload)
        else:
            codes.setdefault(section, {}).update(payload)


# ---------- URL パース・解決 ----------


def _parse_query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query, keep_blank_values=True)


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _norm(code: str, pad: int | None) -> str:
    return code.zfill(pad) if pad else code


def _missing_codes(
    query: dict[str, list[str]], codes: dict, ta: str | None
) -> list[tuple[str, str]]:
    """辞書で解決できない code-dict コードを (section, code) で列挙。"""
    missing: list[tuple[str, str]] = []
    for param, section, pad in _CODE_DICT_SPECS:
        for raw in query.get(param, []):
            code = _norm(raw, pad)
            if _lookup(codes, section, code, ta) is None:
                missing.append((section, code))
    return missing


def _resolve_single(
    query: dict[str, list[str]],
    codes: dict,
    ta: str | None,
    param: str,
    section: str,
    pad: int | None,
    online_keys: set[tuple[str, str]],
) -> ResolvedCode | None:
    raw = _first(query, param)
    if raw is None:
        return None
    code = _norm(raw, pad)
    label = _lookup(codes, section, code, ta)
    return ResolvedCode(param, code, label, (section, code) in online_keys)


def _resolve_multi(
    query: dict[str, list[str]],
    codes: dict,
    ta: str | None,
    param: str,
    section: str,
    online_keys: set[tuple[str, str]],
) -> list[ResolvedCode]:
    out: list[ResolvedCode] = []
    for raw in query.get(param, []):
        label = _lookup(codes, section, raw, ta)
        out.append(ResolvedCode(param, raw, label, (section, raw) in online_keys))
    return out


def explain_url(
    url: str, *, offline: bool = False, codes_path: Path = _DATA_PATH
) -> SuumoSearchExplanation:
    """URL を解釈する。未知コードはオンライン解決して辞書へ追記 (offline=True で抑止)。"""
    query = _parse_query(url)
    codes = load_codes(codes_path)
    ta = _first(query, "ta")

    online_keys: set[tuple[str, str]] = set()
    if not offline:
        missing = [
            (s, c) for s, c in _missing_codes(query, codes, ta) if s in _ONLINE_SECTIONS
        ]
        if missing:
            resolved = _resolve_online(query, missing, ta)
            if resolved:
                _merge_into_codes(codes, resolved, ta)
                save_codes(codes, codes_path)
                online_keys = {
                    (s, c)
                    for s, c in missing
                    if _lookup(codes, s, c, ta) is not None
                }

    return _build_explanation(url, query, codes, ta, online_keys)


def commute_to_station(url: str, *, codes_path: Path = _DATA_PATH) -> str | None:
    """検索 URL の起点駅(ekInput)+所要時間(tj)+乗換(nk) を「渋谷まで: 20分・0回」形式に。

    通勤条件 (ekInput と tj) が無い検索では None を返す。辞書は offline 参照のみ。
    """
    query = _parse_query(url)
    ek = _first(query, "ekInput")
    tj = _first(query, "tj")
    if not ek or not tj:
        return None
    ta = _first(query, "ta")
    codes = load_codes(codes_path)
    station = _lookup(codes, "ek", ek.zfill(5), ta) or f"駅{ek}"
    nk = _first(query, "nk")
    tail = f"・{nk}回" if nk not in (None, "") else ""
    return f"{station}まで: {tj}分{tail}"


def _build_explanation(
    url: str,
    query: dict[str, list[str]],
    codes: dict,
    ta: str | None,
    online_keys: set[tuple[str, str]],
) -> SuumoSearchExplanation:
    region = _resolve_single(query, codes, ta, "ar", "ar", None, online_keys)
    property_kind = _resolve_single(query, codes, ta, "bs", "bs", None, online_keys)
    prefecture = _resolve_single(query, codes, ta, "ta", "ta", None, online_keys)
    sort_order = _resolve_single(query, codes, ta, "po1", "po1", None, online_keys)
    station = _resolve_single(query, codes, ta, "ekInput", "ek", 5, online_keys)
    building_types = _resolve_multi(query, codes, ta, "ts", "ts", online_keys)
    lines = _resolve_multi(query, codes, ta, "rn", "rn", online_keys)
    features = _resolve_multi(query, codes, ta, "tc", "tc", online_keys)

    uncertain, unresolved = _classify_extras(query)

    code_results = [
        c
        for c in (region, property_kind, prefecture, sort_order, station)
        if c is not None
    ] + building_types + lines + features
    newly_resolved = [c for c in code_results if c.resolved_online]

    return SuumoSearchExplanation(
        url=url,
        rent=_fmt_rent(_first(query, "cb"), _first(query, "ct")),
        floor_area=_fmt_floor_area(_first(query, "mb"), _first(query, "mt")),
        walk_minutes=_fmt_walk(_first(query, "et")),
        commute=_fmt_commute(_first(query, "tj"), _first(query, "nk")),
        building_age=_fmt_building_age(_first(query, "cn")),
        region=region,
        property_kind=property_kind,
        prefecture=prefecture,
        sort_order=sort_order,
        station=station,
        building_types=building_types,
        lines=lines,
        features=features,
        uncertain=uncertain,
        unresolved=unresolved,
        newly_resolved=newly_resolved,
    )


# code-dict / scalar として既に扱う param。残りを extras に振り分ける。
_HANDLED_PARAMS = (
    {param for param, _, _ in _CODE_DICT_SPECS}
    | {"cb", "ct", "mb", "mt", "et", "tj", "nk", "cn", "page", "pc"}
    | _IGNORE_PARAMS
    | set(_STRUCTURE_PARAMS)
    | set(_OPAQUE_PARAMS)
)


def _classify_extras(
    query: dict[str, list[str]],
) -> tuple[list[str], list[str]]:
    """不確実 (best-effort) と未解決 (テーブル外) を仕分ける。捏造しない。"""
    uncertain: list[str] = []
    if any(p in query for p in _STRUCTURE_PARAMS):
        uncertain.append("建物構造: 鉄筋系? (shkr)")
    if "kz" in query:
        # kz は _OPAQUE/_STRUCTURE と別。searchdisp 上「管理費・共益費込み」に対応するが帰属未確定。
        uncertain.append(f"管理費・共益費込み? (kz={_first(query, 'kz')})")
    for p in _OPAQUE_PARAMS:
        if p in query:
            uncertain.append(f"{p}={_first(query, p)} (意味未確定)")

    handled = _HANDLED_PARAMS | {"kz"}
    unresolved: list[str] = []
    for param, values in query.items():
        if param in handled:
            continue
        for v in values:
            if v == "":  # 空値は無視 (例: 想定外の fw=)
                continue
            unresolved.append(f"{param}={v}")
    return uncertain, unresolved


# ---------- オンライン解決 (SEO 沿線ページ) ----------


def _http_client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.9"},
        timeout=timeout,
        follow_redirects=True,
    )


def _get_text(client: httpx.Client, url: str) -> str:
    resp = client.get(url)
    resp.raise_for_status()
    return resp.text


def _warn(msg: str) -> None:
    print(f"[warn] {msg}", file=sys.stderr)


def _ensen_url(pref: str) -> str:
    return f"{_SEO_BASE}/{pref}/ensen/"


def _en_url(pref: str, slug: str) -> str:
    return f"{_SEO_BASE}/{pref}/en_{slug}/"


def _parse_ensen(html: str, pref: str) -> dict[str, str]:
    """沿線一覧ページから slug→路線名。最初の出現を採用。"""
    pat = re.compile(
        r'href="/chintai/' + re.escape(pref) + r'/en_([a-z0-9_]+)/"[^>]*>([^<]+)</a>'
    )
    out: dict[str, str] = {}
    for slug, name in pat.findall(html):
        out.setdefault(slug, name.strip())
    return out


def _parse_tc(html: str) -> dict[str, str]:
    return {code: label.strip() for code, label in _TC_RE.findall(html)}


def _parse_ek(html: str) -> dict[str, str]:
    return {code: name.strip() for code, name in _EK_RE.findall(html)}


def _parse_own_rn(html: str) -> str | None:
    """ページ自身の路線 rn コード (検索リンク等での最頻値)。曖昧なら最頻 1 件。"""
    codes = _RN_RE.findall(html)
    if not codes:
        return None
    return Counter(codes).most_common(1)[0][0]


def _resolve_online(
    query: dict[str, list[str]],
    missing: list[tuple[str, str]],
    ta: str | None,
) -> dict:
    """未知の tc/rn/ek を SEO 沿線ページから解決する。部分成功を許容。

    戻り値: {"tc": {code: label}, "rn": {code: name}, "ek": {code: name}}
    (rn/ek は ta 別ネストではなく素の {code: name}。_merge_into_codes が ta を付与)
    """
    pref = _PREF_SLUG.get(ta or "")
    if not pref:
        _warn(f"ta={ta} は SEO スラッグ未対応のためオンライン解決をスキップ")
        return {}

    want_tc = any(s == "tc" for s, _ in missing)
    missing_rn = {c for s, c in missing if s == "rn"}
    missing_ek = {c for s, c in missing if s == "ek"}

    result: dict[str, dict[str, str]] = {}
    with _http_client() as client:
        try:
            index_html = _get_text(client, _ensen_url(pref))
        except httpx.HTTPError as e:
            _warn(f"沿線一覧の取得失敗: {e}")
            return result
        slug_to_name = _parse_ensen(index_html, pref)
        if not slug_to_name:
            _warn("沿線一覧から路線リンクを抽出できませんでした")

        tc_out: dict[str, str] = {}
        rn_out: dict[str, str] = {}
        ek_out: dict[str, str] = {}

        for i, (slug, line_name) in enumerate(slug_to_name.items()):
            done_tc = not want_tc or bool(tc_out)
            done_rn = not missing_rn or missing_rn <= rn_out.keys()
            done_ek = not missing_ek or missing_ek <= ek_out.keys()
            if done_tc and done_rn and done_ek:
                break
            if i >= _MAX_LINE_FETCHES:
                _warn(
                    f"沿線ページ取得上限 ({_MAX_LINE_FETCHES}) に到達。"
                    "未解決コードが残る可能性があります"
                )
                break
            try:
                page = _get_text(client, _en_url(pref, slug))
            except httpx.HTTPError as e:
                _warn(f"沿線ページ取得失敗 ({slug}): {e}")
                continue

            if want_tc and not tc_out:
                tc_out = _parse_tc(page)
            if missing_rn:
                own_rn = _parse_own_rn(page)
                if own_rn in missing_rn:
                    rn_out[own_rn] = line_name
            if missing_ek:
                for code, name in _parse_ek(page).items():
                    if code in missing_ek:
                        ek_out[code] = name

    if tc_out:
        result["tc"] = tc_out
    if rn_out:
        result["rn"] = rn_out
    if ek_out:
        result["ek"] = ek_out
    return result
