"""dry-run 用の検証スクリプト。送信せず Telegram のテキストを直接組む。"""
from __future__ import annotations

from jkkwatcher.models import Property, SuumoProperty, UrProperty
from jkkwatcher.telegram import MESSAGE_SAFE_LIMIT, build_hit_messages

# Telegram sendMessage の text 上限 (これを越えると 400)。
TELEGRAM_HARD_LIMIT = 4096


def make_jkk(name: str) -> Property:
    return Property(
        name=name, area="中央区", priority_type="先着", house_type="一般",
        layout="2LDK", floor_area="50", rent="100000", common_fee="1000",
        units="1", room_id="001", residence_code="R001",
    )


def make_ur(name: str) -> UrProperty:
    return UrProperty(
        name=name, area="世田谷区", address="東京都世田谷区下高井戸1-1-1",
        access="京王線 下高井戸駅 歩4分", layout="1LDK", floor_area="43.38m²",
        floor="3階", rent="136,200円", common_fee="3,000円",
        shisya="20", danchi="123", shikibetu="4", room_id="000003712",
        room_no="101号室",
        detail_url="https://www.ur-net.go.jp/chintai/detail/20_123_4/",
    )


def make_suumo(name: str) -> SuumoProperty:
    return SuumoProperty(
        name=name, area="世田谷区", address="東京都世田谷区下高井戸1-1-1",
        access="東急世田谷線/下高井戸駅 歩4分", age="築6年",
        building_floors="地上5階建", floor="3階", layout="1LDK",
        floor_area="43.38m²", rent="20万円", common_fee="5000円",
        jnc="000012345678", bc="100123456789",
        detail_url="https://suumo.jp/chintai/jnc_000012345678/",
        commute="渋谷駅（7分・0回）",
    )


def summarize(label: str, texts: list[str]) -> None:
    print(f"\n=== {label} ({len(texts)} messages) ===")
    for i, text in enumerate(texts):
        assert len(text) <= TELEGRAM_HARD_LIMIT, (
            f"{label} msg[{i}] は上限超過: {len(text)} > {TELEGRAM_HARD_LIMIT}"
        )
        print(f"--- msg[{i}] {len(text)} chars ---")
        print(text)


# ---------- A. ソース別の基本フォーマット ----------

summarize("A1. jkk", build_hit_messages("jkk", [make_jkk("ウォッチ対象")]))
summarize("A2. ur", build_hit_messages("ur", [make_ur("ウォッチ対象UR")]))
summarize("A3. suumo", build_hit_messages("suumo", [make_suumo("ウォッチ対象Suumo")]))


# ---------- B. HTML エスケープ ----------
# 物件名やクエリ付き URL に & や <> が入っても Telegram が 400 にならないこと。

evil = make_suumo('AT&T <b>ハイツ</b> "本館"')
evil = SuumoProperty(
    **{
        **evil.to_dict(),
        "detail_url": "https://suumo.jp/chintai/jnc_1/?a=1&b=2",
    }
)
texts = build_hit_messages("suumo", [evil])
summarize("B. escaping", texts)
assert "&amp;" in texts[0] and "&lt;b&gt;" in texts[0], "エスケープされていない"
assert "<b>" in texts[0] and "<a href=" in texts[0], "整形タグまで潰れている"
assert "?a=1&amp;b=2" in texts[0], "URL の & がエスケープされていない"


# ---------- C. ヒット 0 件 ----------

assert build_hit_messages("jkk", []) == [], "ヒット 0 件で空リストにならない"
print("\n=== C. no hits -> [] OK ===")


# ---------- D. 上限を越える件数の分割 ----------

many = [make_suumo(f"分割テスト物件{i:03d}") for i in range(200)]
split = build_hit_messages("suumo", many)
print(f"\n=== D. split ({len(many)} hits -> {len(split)} messages) ===")
assert len(split) > 1, "分割されていない"
for i, text in enumerate(split):
    print(f"  msg[{i}] {len(text)} chars")
    assert len(text) <= TELEGRAM_HARD_LIMIT, f"msg[{i}] が Telegram の上限を超過"
# 全ヒットが 1 件も落ちていないこと (Slack と違い件数で打ち切らない)。
joined = "\n".join(split)
missing = [p.name for p in many if p.name not in joined]
assert not missing, f"欠落した物件: {missing[:5]}"
assert all(f"({i + 1}/{len(split)})" in t for i, t in enumerate(split)), (
    "分割時の連番ヘッダが付いていない"
)
print(f"  全 {len(many)} 件が欠落なし / safe limit={MESSAGE_SAFE_LIMIT} OK")

print("\nすべての検証を通過しました。")
