"""SUUMO 重複排除 (実名/仮名マージ) の検証スクリプト (ネット不要・オフライン)。

`uv run python scripts/verify_suumo_dedup.py` で実行。失敗があれば exit 1。
"""
from __future__ import annotations

import sys

from jkkwatcher.models import (
    SuumoProperty,
    _is_auto_generated_suumo_name,
    select_suumo_representatives,
)

_failures: list[str] = []


def check(label: str, cond: bool, got: object = None) -> None:
    if cond:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}  got={got!r}")
        _failures.append(label)


def make(
    name: str,
    *,
    area: str = "世田谷区",
    age: str = "築6年",
    floor: str = "3階",
    layout: str = "1LDK",
    floor_area: str = "43.38m²",
    rent: str = "20万円",
    bc: str = "bc0",
) -> SuumoProperty:
    """key に効くフィールドだけ可変にした SuumoProperty ファクトリ。"""
    return SuumoProperty(
        name=name, area=area, address="", access="", age=age,
        building_floors="地上5階建", floor=floor, layout=layout,
        floor_area=floor_area, rent=rent, common_fee="-",
        jnc="jnc0", bc=bc, detail_url="",
    )


# ---------- 1. 新 key (name 非依存) の同一視 / 弁別 ----------

print("\n=== 1. 新 key (name 非依存) ===")
real = make("ファインスクェア明大前", bc="bcA")
alias = make("京王線 下高井戸駅 5階建 築6年", bc="bcB")
check("実名と仮名が同一 key (name を無視)", real.key == alias.key, (real.key, alias.key))
check("面積が小数2桁違うと別 key", real.key != make("X", floor_area="43.39m²").key)
check("賃料違いは別 key", real.key != make("X", rent="21万円").key)
check("所在階違いは別 key", real.key != make("X", floor="4階").key)
check("築年違いは別 key", real.key != make("X", age="築7年").key)
check("間取り違いは別 key", real.key != make("X", layout="2LDK").key)
check("区違いは別 key", real.key != make("X", area="渋谷区").key)


# ---------- 2. 仮名検出 ----------

print("\n=== 2. 仮名検出 ===")
for nm in [
    "京王線 下高井戸駅 5階建 築6年",
    "ＪＲ中央線 西荻窪駅 3階建",
    "都営大江戸線 豊島園駅 地下1地上5階建 築26年",
]:
    check(f"仮名と判定: {nm}", _is_auto_generated_suumo_name(nm) is True, nm)
for nm in [
    "ファインスクェア明大前",
    "ディアマークスキャピタルタワー",
    "京王プラザマンション",  # 京王 を含むが 駅/階建 の骨格なし
    "グランドメゾン5階建",  # 路線/駅 prefix なし
    "",
]:
    check(
        f"実名と判定 (仮名でない): {nm!r}",
        _is_auto_generated_suumo_name(nm) is False,
        nm,
    )


# ---------- 3. 代表選択 (実名優先・順序安定) ----------

print("\n=== 3. 代表選択 ===")
group = [
    make("京王線 下高井戸駅 5階建 築6年", bc="b1"),
    make("ファインスクェア明大前", bc="b2"),
    make("京王線 下高井戸駅 5階建 築6年", bc="b3"),
]
reps = select_suumo_representatives(group)
check("同一 key は 1 件に集約", len(reps) == 1, len(reps))
check(
    "代表は実名",
    bool(reps) and reps[0].name == "ファインスクェア明大前",
    reps[0].name if reps else None,
)

g2 = [make("実名A", bc="a"), make("実名B", bc="b")]
reps2 = select_suumo_representatives(g2)
check("実名複数なら first-seen", len(reps2) == 1 and reps2[0].name == "実名A", reps2[0].name)

g3 = [
    make("京王線 下高井戸駅 5階建 築6年", bc="x"),
    make("小田急線 成城学園前駅 3階建", bc="y"),
]
reps3 = select_suumo_representatives(g3)
check("仮名のみは first-seen 先頭", len(reps3) == 1 and reps3[0].bc == "x", reps3[0].bc)

m_alias = make("京王線 下高井戸駅 5階建", rent="20万円", bc="k1a")
m_real1 = make("ファインA", rent="20万円", bc="k1b")
m_real2 = make("ファインB", rent="25万円", bc="k2")
reps4 = select_suumo_representatives([m_alias, m_real1, m_real2])
check("複数 key → 2 グループ", len(reps4) == 2, len(reps4))
check(
    "key 初出順を保つ",
    len(reps4) == 2 and reps4[0].key == m_alias.key,
    [r.key for r in reps4],
)
check(
    "各グループで実名代表",
    len(reps4) == 2 and reps4[0].name == "ファインA" and reps4[1].name == "ファインB",
    [r.name for r in reps4],
)


# ---------- 4. 空 area / age のエッジ ----------

print("\n=== 4. 空フィールドのエッジ ===")
e1 = make("X", area="", bc="e1")
e2 = make("Y", area="", bc="e2")
check("area 空同士は同一 key", e1.key == e2.key, (e1.key, e2.key))
check("area 空 と 非空 は別 key", e1.key != make("Z", area="世田谷区").key)
check("age 空同士は同一 key", make("X", age="").key == make("Y", age="").key)
check(
    "age 空 と 築6年 は別 key (空はワイルドカードでない)",
    make("X", age="").key != make("Z", age="築6年").key,
)


# ---------- 結果 ----------

print(f"\n=== 結果: {len(_failures)} 件失敗 ===")
if _failures:
    for f in _failures:
        print(f"  - {f}")
    sys.exit(1)
print("すべて OK")
