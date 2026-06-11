"""`suumo explain` の検証スクリプト (ネット不要・オフライン)。

`uv run python scripts/verify_suumo_explain.py` で実行。失敗があれば exit 1。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from jkkwatcher.cli import SUUMO_URL_ENVVAR, _resolve_suumo_url
from jkkwatcher.line_emoji import route_emoji
from jkkwatcher.models import SuumoProperty
from jkkwatcher.notifier import (
    _STATION_SEP,
    _SUMMARY_INDENT,
    _format_commute,
    _format_suumo_access,
    _suumo_floors,
    _suumo_summary_line,
)
from jkkwatcher.station_lines import StationLineIndex
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
check("通勤=渋谷まで: 20分以内 / 乗換なし (目的駅統合)", exp.commute == "渋谷まで: 20分以内 / 乗換なし", exp.commute)
check(
    "commute_to_station(DEFAULT)=='渋谷まで: 20分以内 / 乗換なし'",
    commute_to_station(DEFAULT_SEARCH_URL) == "渋谷まで: 20分以内 / 乗換なし",
    commute_to_station(DEFAULT_SEARCH_URL),
)
check(
    "ekInput 無し URL は None (素の通勤条件にフォールバック)",
    commute_to_station("https://suumo.jp/jj/chintai/ichiran/FR301FC001/?ar=030&ta=13&tj=20&nk=0") is None,
    commute_to_station("https://suumo.jp/jj/chintai/ichiran/FR301FC001/?ar=030&ta=13&tj=20&nk=0"),
)
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
    "_format_commute('渋谷駅（20分・0回）')=='渋谷まで: 20分/0回'",
    _format_commute("渋谷駅（20分・0回）") == "渋谷まで: 20分/0回",
    _format_commute("渋谷駅（20分・0回）"),
)
check("_format_commute('') == ''", _format_commute("") == "", _format_commute(""))
check(
    "_format_commute 非該当は原文のまま",
    _format_commute("バス15分") == "バス15分",
    _format_commute("バス15分"),
)

# --- 路線名 → 絵文字 マッピング ---
check("全角JR正規化 ＪＲ山手線→jr-yamanote", route_emoji("ＪＲ山手線") == "jr-yamanote", route_emoji("ＪＲ山手線"))
check("エイリアス 小田急線→odakyu", route_emoji("小田急線") == "odakyu", route_emoji("小田急線"))
check("エイリアス 京王新線→keio", route_emoji("京王新線") == "keio", route_emoji("京王新線"))
check("新規 東急目黒線→tokyu-meguro", route_emoji("東急目黒線") == "tokyu-meguro", route_emoji("東急目黒線"))
check("未登録→None (つくばエクスプレス)", route_emoji("つくばエクスプレス") is None, route_emoji("つくばエクスプレス"))

# --- access の絵文字化 (辞書補完なしのベースライン: 空 index を注入) ---
# 既存挙動は「Suumo 提示路線のみ」。同梱辞書が将来増えても壊れないよう、補完を
# 行わない空 index を明示注入して期待値を固定する。
EMPTY_INDEX = StationLineIndex({})
raw_access = "西武有楽町線/練馬駅 歩3分 / 西武豊島線/豊島園駅 歩11分 / 都営大江戸線/豊島園駅 歩11分"
check(
    "access 集約 (駅単位でロゴ連結, 西武→seibu-ikebukuro エイリアス)",
    _format_suumo_access(raw_access, station_lines=EMPTY_INDEX)
    == [":seibu-ikebukuro: 練馬駅 歩3分", ":seibu-ikebukuro::toei-oedo: 豊島園駅 歩11分"],
    _format_suumo_access(raw_access, station_lines=EMPTY_INDEX),
)
check(
    "access 空 → []",
    _format_suumo_access("", station_lines=EMPTY_INDEX) == [],
    _format_suumo_access("", station_lines=EMPTY_INDEX),
)
check(
    "ロゴ重複除去 (京王線+京王新線→:keio:1つ)",
    _format_suumo_access("京王線/笹塚駅 歩3分 / 京王新線/笹塚駅 歩3分", station_lines=EMPTY_INDEX)
    == [":keio: 笹塚駅 歩3分"],
    _format_suumo_access("京王線/笹塚駅 歩3分 / 京王新線/笹塚駅 歩3分", station_lines=EMPTY_INDEX),
)
check(
    "ロゴ+未登録テキスト混在 (絵文字→テキスト名→駅 の順)",
    _format_suumo_access("東急目黒線/大岡山駅 歩1分 / 東急池上線/大岡山駅 歩1分", station_lines=EMPTY_INDEX)
    == [":tokyu-meguro: 東急池上線 大岡山駅 歩1分"],
    _format_suumo_access("東急目黒線/大岡山駅 歩1分 / 東急池上線/大岡山駅 歩1分", station_lines=EMPTY_INDEX),
)
check(
    "全角JRの駅情報",
    _format_suumo_access("ＪＲ山手線/渋谷駅 歩5分", station_lines=EMPTY_INDEX)
    == [":jr-yamanote: 渋谷駅 歩5分"],
    _format_suumo_access("ＪＲ山手線/渋谷駅 歩5分", station_lines=EMPTY_INDEX),
)
check(
    "パース不能エントリは原文のまま末尾に残す",
    _format_suumo_access("京王線/明大前駅 歩10分 / バス20分 北口", station_lines=EMPTY_INDEX)
    == [":keio: 明大前駅 歩10分", "バス20分 北口"],
    _format_suumo_access("京王線/明大前駅 歩10分 / バス20分 北口", station_lines=EMPTY_INDEX),
)

# --- 全路線補完 (小さなテスト用 index を注入) ---
# Suumo が一部路線しか出さない駅を、アンカー路線で同定して全路線に広げる。
TEST_INDEX = StationLineIndex(
    {
        "下高井戸": [{"lines": ["京王線", "東急世田谷線"], "pref_cd": 13, "station_g_cd": 1}],
        "明大前": [{"lines": ["京王線", "京王井の頭線"], "pref_cd": 13, "station_g_cd": 2}],
        # 同名異駅: 本町 (大阪・架空) と 本町 (東京・京王線) をアンカーで一意化。
        "本町": [
            {"lines": ["大阪メトロ御堂筋線", "大阪メトロ四つ橋線"], "pref_cd": 27, "station_g_cd": 3},
            {"lines": ["京王線", "テスト線"], "pref_cd": 13, "station_g_cd": 4},
        ],
    }
)
check(
    "補完: 下高井戸 (世田谷線のみ提示) → 京王線を末尾に追加",
    _format_suumo_access("東急世田谷線/下高井戸駅 歩4分", station_lines=TEST_INDEX)
    == [":tokyu-setagaya::keio: 下高井戸駅 歩4分"],
    _format_suumo_access("東急世田谷線/下高井戸駅 歩4分", station_lines=TEST_INDEX),
)
check(
    "補完: 明大前 (京王線のみ提示) → 井の頭線を追加",
    _format_suumo_access("京王線/明大前駅 歩9分", station_lines=TEST_INDEX)
    == [":keio::keio-inokashira: 明大前駅 歩9分"],
    _format_suumo_access("京王線/明大前駅 歩9分", station_lines=TEST_INDEX),
)
check(
    "complete: 提示順を保ち補完分を末尾に",
    TEST_INDEX.complete(["東急世田谷線"], "下高井戸駅") == ["東急世田谷線", "京王線"],
    TEST_INDEX.complete(["東急世田谷線"], "下高井戸駅"),
)
check(
    "complete: 同名異駅をアンカー(京王線)で東京側に一意特定",
    TEST_INDEX.complete(["京王線"], "本町駅") == ["京王線", "テスト線"],
    TEST_INDEX.complete(["京王線"], "本町駅"),
)
check(
    "complete: 辞書に無い駅は現状維持",
    TEST_INDEX.complete(["JR山手線"], "新宿駅") == ["JR山手線"],
    TEST_INDEX.complete(["JR山手線"], "新宿駅"),
)
check(
    "complete: アンカー不一致は現状維持",
    TEST_INDEX.complete(["東京メトロ丸ノ内線"], "下高井戸駅") == ["東京メトロ丸ノ内線"],
    TEST_INDEX.complete(["東京メトロ丸ノ内線"], "下高井戸駅"),
)
check(
    "identify: 引けない駅は None",
    TEST_INDEX.identify("存在しない駅", ["京王線"]) is None,
    TEST_INDEX.identify("存在しない駅", ["京王線"]),
)
check(
    "空 index は常に補完なし",
    EMPTY_INDEX.complete(["京王線"], "下高井戸駅") == ["京王線"],
    None,
)

check("floors '6階'+'地下1地上35階建' → '6/35階'", _suumo_floors("6階", "地下1地上35階建") == "6/35階", _suumo_floors("6階", "地下1地上35階建"))
check("floors '2階'+'11階建' → '2/11階'", _suumo_floors("2階", "11階建") == "2/11階", _suumo_floors("2階", "11階建"))
check("floors 所在階のみ → 'N階'", _suumo_floors("3階", "") == "3階", _suumo_floors("3階", ""))

prop = SuumoProperty(
    name="ディアマークスキャピタルタワー", area="練馬区", address="",
    access=raw_access, age="築26年", building_floors="地下1地上35階建", floor="6階",
    layout="1LDK", floor_area="52.96m²", rent="18万円", common_fee="-",
    jnc="x", bc="y", detail_url="", commute="渋谷駅（20分・0回）",
)
expected = "\n".join([
    "•  *ディアマークスキャピタルタワー* (練馬区)",
    _SUMMARY_INDENT + "18万円 ・ 52.96m² ・ 6/35階 ・ 築26年 ・ 渋谷まで: 20分/0回",
    _SUMMARY_INDENT + _STATION_SEP.join([
        ":seibu-ikebukuro: 練馬駅 歩3分",
        ":seibu-ikebukuro::toei-oedo: 豊島園駅 歩11分",
    ]),
])
got = _suumo_summary_line(prop, station_lines=EMPTY_INDEX)
check("summary 行が 3 行新フォーマットに一致 (物件別 commute)", got == expected, got)
prop_no_commute = SuumoProperty(
    name="X", area="中央区", address="", access="", age="", building_floors="",
    floor="2階", layout="1K", floor_area="25m²", rent="9万円", common_fee="-",
    jnc="x", bc="y", detail_url="",
)
check(
    "commute 空なら通勤を出さない",
    "まで:" not in _suumo_summary_line(prop_no_commute, station_lines=EMPTY_INDEX),
    _suumo_summary_line(prop_no_commute, station_lines=EMPTY_INDEX),
)
check(
    "access 空なら駅行を出さない (2 行のみ)",
    _suumo_summary_line(prop_no_commute, station_lines=EMPTY_INDEX).count("\n") == 1,
    _suumo_summary_line(prop_no_commute, station_lines=EMPTY_INDEX),
)


# --- station_lines ローダ round-trip (一時 JSON) ---
print("\n=== 6. station_lines ローダ ===")
with tempfile.TemporaryDirectory() as _d:
    _p = Path(_d) / "station_lines.json"
    _p.write_text(
        json.dumps(
            {"stations": {"下高井戸": [{"lines": ["京王線", "東急世田谷線"], "pref_cd": 13}]}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    check(
        "load: JSON から読んで identify できる",
        StationLineIndex.load(_p).identify("下高井戸駅", ["京王線"])
        == ["京王線", "東急世田谷線"],
        StationLineIndex.load(_p).identify("下高井戸駅", ["京王線"]),
    )
check(
    "load: 不在パスは空 index に縮退 (補完なし)",
    StationLineIndex.load(Path("/nonexistent/xxx.json")).complete(["京王線"], "下高井戸駅")
    == ["京王線"],
    None,
)


# ---------- 結果 ----------

print(f"\n=== 結果: {len(_failures)} 件失敗 ===")
if _failures:
    for f in _failures:
        print(f"  - {f}")
    sys.exit(1)
print("すべて OK")
