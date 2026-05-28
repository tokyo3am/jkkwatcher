"""dry-run 用の検証スクリプト。スクレイプせず payload を直接組む。"""
from __future__ import annotations

from jkkwatcher.diff import Diff
from jkkwatcher.models import Property, SuumoProperty, UrProperty
from jkkwatcher.notifier import (
    build_jkk_messages,
    build_suumo_messages,
    build_ur_messages,
)
from jkkwatcher.watchlist import NotifyConfig, WatchlistEntry


def make_jkk(residence_code: str, room_id: str, name: str) -> Property:
    return Property(
        name=name, area="中央区", priority_type="先着", house_type="一般",
        layout="2LDK", floor_area="50", rent="100000", common_fee="1000",
        units="1", room_id=room_id, residence_code=residence_code,
    )


def summarize_messages(label: str, messages: list[dict]) -> None:
    print(f"\n=== {label} ({len(messages)} messages) ===")
    for i, m in enumerate(messages):
        blocks = m["blocks"]
        text = m["text"]
        max_section = 0
        for b in blocks:
            t = b.get("text", {})
            if isinstance(t, dict):
                max_section = max(max_section, len(t.get("text", "")))
        print(
            f"  msg[{i}] blocks={len(blocks):2d} max_section_text={max_section:4d} text={text!r}"
        )


# ---------- 基本シナリオ (hits + others + removed) ----------

p_hit = make_jkk("WATCH", "001", "ウォッチ対象")
p_other1 = make_jkk("OTHER1", "002", "その他物件1")
p_other2 = make_jkk("OTHER2", "003", "その他物件2")
p_removed = make_jkk("REM1", "004", "終了物件")

diff = Diff(added=[p_hit, p_other1, p_other2], removed=[p_removed])
current = [p_hit, p_other1, p_other2]

cfg_default = NotifyConfig()
summarize_messages("A. default cfg", build_jkk_messages(diff, current, notify_config=cfg_default))

cfg_hit = NotifyConfig(
    mention_on_watch_hit=True,
    watchlist=(WatchlistEntry(source="jkk", building_key="WATCH"),),
)
summarize_messages("B. watchlist hit", build_jkk_messages(diff, current, notify_config=cfg_hit))

cfg_added = NotifyConfig(mention_on_added=True, mention_on_watch_hit=False)
summarize_messages("C. on_added only", build_jkk_messages(diff, current, notify_config=cfg_added))

cfg_both = NotifyConfig(
    mention_on_added=True,
    mention_on_watch_hit=True,
    watchlist=(WatchlistEntry(source="jkk", building_key="WATCH"),),
)
summarize_messages("D. both true (hit priority)", build_jkk_messages(diff, current, notify_config=cfg_both))

# E. added 無し → hits/diff メッセージは無く、current_summary だけ
diff_no_add = Diff(added=[], removed=[p_removed])
summarize_messages("E. removed only", build_jkk_messages(diff_no_add, [p_other1], notify_config=cfg_added))

# ---------- current_summary 大量 → 複数 section ----------

# UR: 大量物件で複数 section に分割される想定
ur_lots = [
    UrProperty(
        name=f"UR物件名{i:03d}メッシュリッチプラザザタワー",  # 名前長め
        area="中央区", address="長めの住所" * 3, access="アクセス情報" * 2,
        layout="2LDK", floor_area="60m²", floor=f"{i % 30 + 1}",
        rent="200,000円", common_fee="5,000円",
        shisya="30", danchi="503", shikibetu=f"{i:03d}", room_id=f"r{i}",
        room_no=f"{i:03d}",
        detail_url=f"https://www.ur-net.go.jp/chintai/detail/30_503_{i:03d}.html",
    )
    for i in range(80)
]
ur_diff = Diff(added=ur_lots[:3], removed=[])
summarize_messages(
    "F. UR 80 件 current_summary",
    build_ur_messages(ur_diff, ur_lots, notify_config=cfg_default),
)

# ---------- Suumo: 24件 added + 5件 removed ----------

suumo_props = [
    SuumoProperty(
        name=f"Suumo物件{i}", area="中央区", address="", access="アクセス情報",
        age="築10年", building_floors="", floor=f"{i + 1}階",
        layout="1LDK", floor_area="40m²", rent="13万円", common_fee="-",
        jnc=f"jnc{i}", bc=f"bc{i}",
        detail_url=f"https://suumo.jp/chintai/jnc_{i:09d}/?bc=100{i:09d}",
    )
    for i in range(43)
]
suumo_diff = Diff(added=suumo_props[:24], removed=suumo_props[24:29])
summarize_messages(
    "G. Suumo 24 added / 5 removed / 43 current",
    build_suumo_messages(suumo_diff, suumo_props, notify_config=cfg_default),
)

# ---------- 上限近傍テスト: 多数の added/removed で trim 確認 ----------

many_added = [make_jkk(f"A{i}", f"r{i}", f"新着{i}") for i in range(40)]
many_removed = [make_jkk(f"R{i}", f"r{i}", f"終了{i}") for i in range(40)]
diff_many = Diff(added=many_added, removed=many_removed)
current_small = many_added[:5]
summarize_messages(
    "H. 40 added / 40 removed (trim test)",
    build_jkk_messages(diff_many, current_small, notify_config=cfg_default),
)

# ---------- Validate: 全 message が Slack 制約内 ----------

def validate(label: str, messages: list[dict]) -> None:
    for i, m in enumerate(messages):
        blocks = m["blocks"]
        if len(blocks) > 50:
            print(f"  FAIL: {label} msg[{i}] has {len(blocks)} blocks (> 50)")
            return
        for j, b in enumerate(blocks):
            t = b.get("text", {})
            if isinstance(t, dict):
                tlen = len(t.get("text", ""))
                if tlen > 3000:
                    print(f"  FAIL: {label} msg[{i}] block[{j}] text {tlen} chars (> 3000)")
                    return
    print(f"  OK: {label} all messages within Slack limits")


print("\n=== Slack 制約バリデーション ===")
validate("A", build_jkk_messages(diff, current, notify_config=cfg_default))
validate("B", build_jkk_messages(diff, current, notify_config=cfg_hit))
validate("F", build_ur_messages(ur_diff, ur_lots, notify_config=cfg_default))
validate("G", build_suumo_messages(suumo_diff, suumo_props, notify_config=cfg_default))
validate("H", build_jkk_messages(diff_many, current_small, notify_config=cfg_default))
