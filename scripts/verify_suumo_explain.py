"""`suumo explain` の検証スクリプト (ネット不要・オフライン)。

`uv run python scripts/verify_suumo_explain.py` で実行。失敗があれば exit 1。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from jkkwatcher.cli import SUUMO_URL_ENVVAR, _resolve_suumo_url
from jkkwatcher.models import SuumoProperty
from jkkwatcher.notifier import (
    _format_suumo_access,
    _suumo_floors,
    _suumo_summary_line,
)
from jkkwatcher.suumo_explain import (
    _fmt_building_age,
    _fmt_commute,
    _fmt_floor_area,
    _fmt_rent,
    _fmt_walk,
    _man_yen,
    _merge_into_codes,
    commute_to_station,
    explain_url,
    load_codes,
    save_codes,
)
from jkkwatcher.suumo_scraper import DEFAULT_SEARCH_URL

_failures: list[str] = []


def check(label: str, cond: bool, got: object = None) -> None:
    if cond:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}  got={got!r}")
        _failures.append(label)


# ---------- 1. オフライン全解決 (DEFAULT_SEARCH_URL) ----------

print("\n=== 1. オフライン解釈 (DEFAULT_SEARCH_URL) ===")
exp = explain_url(DEFAULT_SEARCH_URL, offline=True)

check("都道府県=東京都", exp.prefecture and exp.prefecture.label == "東京都", exp.prefecture)
check("地方=関東", exp.region and exp.region.label == "関東", exp.region)
check("物件種別=賃貸", exp.property_kind and exp.property_kind.label == "賃貸", exp.property_kind)
check(
    "建物種別=マンション",
    [c.label for c in exp.building_types] == ["マンション"],
    exp.building_types,
)
check("沿線=26本", len(exp.lines) == 26, len(exp.lines))
check(
    "沿線すべて解決済み",
    all(c.label is not None for c in exp.lines),
    [c.code for c in exp.lines if c.label is None],
)
check("こだわり=7件", len(exp.features) == 7, len(exp.features))
check(
    "こだわりすべて解決済み",
    all(c.label is not None for c in exp.features),
    [c.code for c in exp.features if c.label is None],
)
check("駅=渋谷", exp.station and exp.station.label == "渋谷", exp.station)
check("賃料=〜20万円", exp.rent == "〜20万円", exp.rent)
check("専有面積=40m²以上", exp.floor_area == "40m²以上", exp.floor_area)
check("駅徒歩=徒歩10分以内", exp.walk_minutes == "徒歩10分以内", exp.walk_minutes)
check("通勤=20分以内 / 乗換なし", exp.commute == "20分以内 / 乗換なし", exp.commute)
check("築年数=None (9999999)", exp.building_age is None, exp.building_age)
check("並び順=専有面積が広い順", exp.sort_order and exp.sort_order.label == "専有面積が広い順", exp.sort_order)
check("未解決パラメータなし", exp.unresolved == [], exp.unresolved)
check("不確実枠あり (shkr/kz/smk/co)", len(exp.uncertain) > 0, exp.uncertain)
check("オフラインなので追記なし", exp.newly_resolved == [], exp.newly_resolved)

# ---------- 2. scalar 整形 ----------

print("\n=== 2. scalar 整形 ===")
check("_man_yen('0.0') is None", _man_yen("0.0") is None, _man_yen("0.0"))
check("_man_yen('20.0')=='20万円'", _man_yen("20.0") == "20万円", _man_yen("20.0"))
check("_man_yen('9.5')=='9.5万円'", _man_yen("9.5") == "9.5万円", _man_yen("9.5"))
check("_fmt_rent('0.0','20.0')=='〜20万円'", _fmt_rent("0.0", "20.0") == "〜20万円", _fmt_rent("0.0", "20.0"))
check("_fmt_rent('9.0','20.0')=='9万円〜20万円'", _fmt_rent("9.0", "20.0") == "9万円〜20万円", _fmt_rent("9.0", "20.0"))
check("_fmt_floor_area('40',None)=='40m²以上'", _fmt_floor_area("40", None) == "40m²以上", _fmt_floor_area("40", None))
check("_fmt_walk('10')=='徒歩10分以内'", _fmt_walk("10") == "徒歩10分以内", _fmt_walk("10"))
check("_fmt_commute('20','0')=='20分以内 / 乗換なし'", _fmt_commute("20", "0") == "20分以内 / 乗換なし", _fmt_commute("20", "0"))
check("_fmt_building_age('9999999') is None", _fmt_building_age("9999999") is None, _fmt_building_age("9999999"))
check("_fmt_building_age('10')=='築10年以内'", _fmt_building_age("10") == "築10年以内", _fmt_building_age("10"))

# ---------- 3. URL resolver (CLI > env > default) ----------

print("\n=== 3. _resolve_suumo_url ===")
_saved = os.environ.get(SUUMO_URL_ENVVAR)
try:
    os.environ.pop(SUUMO_URL_ENVVAR, None)
    check("env 未設定 + None → default", _resolve_suumo_url(None) == DEFAULT_SEARCH_URL, _resolve_suumo_url(None))

    os.environ[SUUMO_URL_ENVVAR] = "https://env.example/"
    check("env 設定 + None → env", _resolve_suumo_url(None) == "https://env.example/", _resolve_suumo_url(None))
    check("env 設定 + '' → env (空文字フォールバック)", _resolve_suumo_url("") == "https://env.example/", _resolve_suumo_url(""))
    check("env 設定 + CLI → CLI 優先", _resolve_suumo_url("https://cli/") == "https://cli/", _resolve_suumo_url("https://cli/"))
finally:
    if _saved is None:
        os.environ.pop(SUUMO_URL_ENVVAR, None)
    else:
        os.environ[SUUMO_URL_ENVVAR] = _saved

# ---------- 4. write-back (merge + round-trip) ----------

print("\n=== 4. write-back ===")
codes: dict = {}
_merge_into_codes(
    codes,
    {"tc": {"0499999": "テスト条件"}, "rn": {"0001": "テスト線"}, "ek": {"00001": "テスト駅"}},
    ta="13",
)
check("tc フラット追記", codes.get("tc", {}).get("0499999") == "テスト条件", codes.get("tc"))
check("rn ta別ネスト追記", codes.get("rn", {}).get("13", {}).get("0001") == "テスト線", codes.get("rn"))
check("ek ta別ネスト追記", codes.get("ek", {}).get("13", {}).get("00001") == "テスト駅", codes.get("ek"))

with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "codes.json"
    saved_ok = save_codes(codes, p)
    check("save_codes 成功", saved_ok is True, saved_ok)
    check("save→load round-trip 一致", load_codes(p) == codes, None)

# ---------- 5. Suumo サマリ新フォーマット ----------

print("\n=== 5. サマリ新フォーマット ===")
check(
    "commute_to_station(DEFAULT)=='渋谷まで: 20分・0回'",
    commute_to_station(DEFAULT_SEARCH_URL) == "渋谷まで: 20分・0回",
    commute_to_station(DEFAULT_SEARCH_URL),
)
check(
    "ekInput/tj 無し URL は None",
    commute_to_station("https://suumo.jp/jj/chintai/ichiran/FR301FC001/?ar=030&ta=13") is None,
    commute_to_station("https://suumo.jp/jj/chintai/ichiran/FR301FC001/?ar=030&ta=13"),
)

raw_access = "西武有楽町線/練馬駅 歩3分 / 西武豊島線/豊島園駅 歩11分 / 都営大江戸線/豊島園駅 歩11分"
check(
    "access 集約 (駅単位で路線を / 連結)",
    _format_suumo_access(raw_access)
    == ["西武有楽町線/練馬駅 歩3分", "西武豊島線/都営大江戸線/豊島園駅 歩11分"],
    _format_suumo_access(raw_access),
)
check("access 空 → []", _format_suumo_access("") == [], _format_suumo_access(""))

check("floors '6階'+'地下1地上35階建' → '6/35階'", _suumo_floors("6階", "地下1地上35階建") == "6/35階", _suumo_floors("6階", "地下1地上35階建"))
check("floors '2階'+'11階建' → '2/11階'", _suumo_floors("2階", "11階建") == "2/11階", _suumo_floors("2階", "11階建"))
check("floors 所在階のみ → 'N階'", _suumo_floors("3階", "") == "3階", _suumo_floors("3階", ""))

prop = SuumoProperty(
    name="ディアマークスキャピタルタワー", area="練馬区", address="",
    access=raw_access, age="築26年", building_floors="地下1地上35階建", floor="6階",
    layout="1LDK", floor_area="52.96m²", rent="18万円", common_fee="-",
    jnc="x", bc="y", detail_url="",
)
expected = (
    "• *ディアマークスキャピタルタワー* (練馬区) / 18万円 / 52.96m² / 6/35階 / 築26年"
    " / 西武有楽町線/練馬駅 歩3分 / 西武豊島線/都営大江戸線/豊島園駅 歩11分"
    " / 渋谷まで: 20分・0回"
)
got = _suumo_summary_line(prop, commute="渋谷まで: 20分・0回")
check("summary 行が新フォーマットに一致", got == expected, got)


# ---------- 結果 ----------

print(f"\n=== 結果: {len(_failures)} 件失敗 ===")
if _failures:
    for f in _failures:
        print(f"  - {f}")
    sys.exit(1)
print("すべて OK")
