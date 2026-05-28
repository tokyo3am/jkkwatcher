"""dry-run 用の検証スクリプト。スクレイプせず payload を直接組む。"""
from __future__ import annotations

from jkkwatcher.diff import Diff
from jkkwatcher.models import Property, SuumoProperty, UrProperty
from jkkwatcher.notifier import build_payload, build_suumo_payload, build_ur_payload
from jkkwatcher.watchlist import NotifyConfig, WatchlistEntry


def make_jkk(residence_code: str, room_id: str, name: str) -> Property:
    return Property(
        name=name, area="中央区", priority_type="先着", house_type="一般",
        layout="2LDK", floor_area="50", rent="100000", common_fee="1000",
        units="1", room_id=room_id, residence_code=residence_code,
    )


def first_block_text(payload: dict) -> str:
    block = payload["blocks"][0]
    t = block.get("text", {})
    if isinstance(t, dict):
        return t.get("text", "")
    return str(t)


def scenario(name: str, payload: dict) -> None:
    print(f"--- {name}")
    print(f"  text   : {payload['text']!r}")
    print(f"  block0 : {first_block_text(payload)!r}")
    print(f"  blocks : {len(payload['blocks'])}")


# JKK 物件: WATCH 一致 1 件 + OTHER 1 件
p_hit = make_jkk("WATCH", "001", "ウォッチ対象")
p_other = make_jkk("OTHER", "002", "その他物件")
diff = Diff(added=[p_hit, p_other], removed=[])

# A. config なし (デフォルト動作: mention_on_watch_hit=True だが watchlist 空)
scenario("A. no config (default)", build_payload(diff, [p_hit, p_other]))

# B. ウォッチリスト一致あり (watch_hit=True)
cfg_hit = NotifyConfig(
    mention_on_watch_hit=True,
    watchlist=(WatchlistEntry(source="jkk", building_key="WATCH"),),
)
scenario("B. watchlist hit", build_payload(diff, [p_hit, p_other], notify_config=cfg_hit))

# C. 全件メンション (on_added=True, watch=False)
cfg_added = NotifyConfig(mention_on_added=True, mention_on_watch_hit=False)
scenario("C. on_added", build_payload(diff, [p_hit, p_other], notify_config=cfg_added))

# D. 両方 true: hit が優先される
cfg_both = NotifyConfig(
    mention_on_added=True,
    mention_on_watch_hit=True,
    watchlist=(WatchlistEntry(source="jkk", building_key="WATCH"),),
)
scenario("D. both true (hit priority)", build_payload(diff, [p_hit, p_other], notify_config=cfg_both))

# E. added 0 件 (removed のみ): メンション無し
diff_no_add = Diff(added=[], removed=[p_hit])
scenario("E. removed only", build_payload(diff_no_add, [p_other], notify_config=cfg_added))

# F. UR と Suumo も同様に動作することを確認
ur_p = UrProperty(
    name="UR対象", area="中央区", address="", access="", layout="1LDK",
    floor_area="40", floor="3", rent="120000", common_fee="2000",
    shisya="30", danchi="503", shikibetu="2", room_id="r",
    room_no="101", detail_url="https://example.com",
)
cfg_ur = NotifyConfig(
    mention_on_watch_hit=True,
    watchlist=(WatchlistEntry(source="ur", building_key="30_503_2"),),
)
scenario("F. UR hit", build_ur_payload(Diff(added=[ur_p], removed=[]), [ur_p], notify_config=cfg_ur))

suumo_p = SuumoProperty(
    name="Suumo対象", area="中央区", address="", access="", age="築10年",
    building_floors="", floor="3階", layout="1LDK", floor_area="40m²",
    rent="13万円", common_fee="", jnc="J1", bc="B123",
    detail_url="https://example.com",
)
cfg_suumo = NotifyConfig(
    mention_on_watch_hit=True,
    watchlist=(WatchlistEntry(source="suumo", building_key="B123"),),
)
scenario(
    "G. Suumo hit",
    build_suumo_payload(Diff(added=[suumo_p], removed=[]), [suumo_p], notify_config=cfg_suumo),
)

# H. name_contains: 物件名 "コーシャハイム世田谷" の部分一致
p_name_hit = make_jkk("ANY1", "010", "コーシャハイム世田谷ⅡA")
p_name_other = make_jkk("ANY2", "011", "都営アパート")
cfg_name = NotifyConfig(
    mention_on_watch_hit=True,
    watchlist=(WatchlistEntry(source="jkk", name_contains="コーシャハイム"),),
)
scenario(
    "H. name_contains hit",
    build_payload(
        Diff(added=[p_name_hit, p_name_other], removed=[]),
        [p_name_hit, p_name_other],
        notify_config=cfg_name,
    ),
)

# I. name_contains 大文字小文字無視
p_ur_case = UrProperty(
    name="UR Shibuya Tower", area="渋谷区", address="", access="", layout="1LDK",
    floor_area="40", floor="3", rent="120000", common_fee="2000",
    shisya="30", danchi="503", shikibetu="2", room_id="r",
    room_no="101", detail_url="https://example.com",
)
cfg_case = NotifyConfig(
    mention_on_watch_hit=True,
    watchlist=(WatchlistEntry(source="ur", name_contains="shibuya"),),
)
scenario(
    "I. name_contains case-insensitive",
    build_ur_payload(Diff(added=[p_ur_case], removed=[]), [p_ur_case], notify_config=cfg_case),
)

# J. building_key + name_contains の併用 (別エントリで OR)
cfg_mix = NotifyConfig(
    mention_on_watch_hit=True,
    watchlist=(
        WatchlistEntry(source="jkk", building_key="WATCH"),
        WatchlistEntry(source="jkk", name_contains="コーシャ"),
    ),
)
diff_mix = Diff(added=[p_hit, p_name_hit, p_other], removed=[])
scenario(
    "J. mixed entries (OR)",
    build_payload(diff_mix, [p_hit, p_name_hit, p_other], notify_config=cfg_mix),
)

# K. 不正設定: 1 エントリに両方指定するとエラー
print("--- K. invalid: both fields in one entry")
try:
    WatchlistEntry(source="jkk", building_key="X", name_contains="Y")
    print("  ERROR: expected ValueError")
except ValueError as e:
    print(f"  OK raised: {e}")

# L. 不正設定: from_json でも検出される
print("--- L. invalid via from_json")
try:
    NotifyConfig.from_json(
        '{"watchlist":[{"source":"jkk","building_key":"x","name_contains":"y"}]}'
    )
    print("  ERROR: expected ValueError")
except ValueError as e:
    print(f"  OK raised: {e}")

# M. from_json で name_contains を読み込み
print("--- M. from_json with name_contains")
cfg_loaded = NotifyConfig.from_json(
    '{"watchlist":[{"source":"jkk","name_contains":"コーシャハイム"}]}'
)
print(f"  watchlist[0]: {cfg_loaded.watchlist[0]}")
print(f"  is_hit (hit): {cfg_loaded.is_hit('jkk', p_name_hit)}")
print(f"  is_hit (miss): {cfg_loaded.is_hit('jkk', p_name_other)}")
