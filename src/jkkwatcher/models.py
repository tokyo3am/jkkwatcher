from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


KU_CODES: dict[str, str] = {
    "01": "千代田区", "02": "中央区", "03": "港区", "04": "新宿区",
    "05": "文京区", "06": "台東区", "07": "墨田区", "08": "江東区",
    "09": "品川区", "10": "目黒区", "11": "大田区", "12": "世田谷区",
    "13": "渋谷区", "14": "中野区", "15": "杉並区", "16": "豊島区",
    "17": "北区", "18": "荒川区", "19": "板橋区", "20": "練馬区",
    "21": "足立区", "22": "葛飾区", "23": "江戸川区",
}


# UR 「東京23区」の検索 URL に出てくる skcs (sub-area) コード一覧。
# https://www.ur-net.go.jp/chintai/kanto/tokyo/result/?... の URL を 23 区
# 5 ブロックに分解したもの。値は人間向けの名称。
SKCS_CODES: dict[str, str] = {
    "102": "千代田・中央",
    "108": "新宿",
    "118": "豊島・北・荒川",
    "121": "文京",
    "122": "台東・墨田・江東",
    "109": "品川・目黒・大田",
    "110": "渋谷・中野・杉並",
    "112": "世田谷",
    "115": "練馬",
    "119": "板橋",
    "120": "足立・葛飾・江戸川",
}


class Area(str, Enum):
    KU = "ku"
    SHI = "shi"


@runtime_checkable
class PropertyLike(Protocol):
    """diff/notifier が物件を扱うために必要な最小インターフェース。"""

    @property
    def key(self) -> str: ...

    @property
    def building_key(self) -> str: ...

    @property
    def name(self) -> str: ...

    def to_dict(self) -> dict[str, str | None]: ...


@dataclass(frozen=True, slots=True)
class Property:
    """JKK の物件 (先着順あき家)。"""

    name: str
    area: str
    priority_type: str
    house_type: str
    layout: str
    floor_area: str
    rent: str
    common_fee: str
    units: str
    room_id: str
    residence_code: str
    thumbnail_url: str | None = None

    @property
    def key(self) -> str:
        return f"{self.residence_code}:{self.room_id}"

    @property
    def building_key(self) -> str:
        return self.residence_code

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class UrProperty:
    """UR の空き部屋。bukken (建物) と room (号室) を一行に flatten したもの。"""

    name: str               # 物件名 (danchiNm)
    area: str               # 住所から抽出した区 (例: "中央区")
    address: str            # 物件住所 (place)
    access: str             # アクセス (HTML タグを除去)
    layout: str             # 間取り (type, 例: "1LDK")
    floor_area: str         # 床面積 (floorspace)
    floor: str              # 階 (floor)
    rent: str               # 家賃 (例: "136,200円")
    common_fee: str         # 共益費 (commonfee)
    shisya: str             # 支社コード
    danchi: str             # 団地コード
    shikibetu: str          # 識別コード
    room_id: str            # 部屋 ID (JKSS, 例: "000003712")
    room_no: str            # 号室 (roomNo)
    detail_url: str         # 詳細ページ URL (絶対)
    thumbnail_url: str | None = None

    @property
    def key(self) -> str:
        # 建物単位 + 部屋 ID で一意。room_id (JKSS) は基本ユニークだが
        # 念のため建物のコンパウンドキーを前置する。
        return f"UR:{self.shisya}_{self.danchi}_{self.shikibetu}:{self.room_id}"

    @property
    def building_key(self) -> str:
        return f"{self.shisya}_{self.danchi}_{self.shikibetu}"

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SuumoProperty:
    """Suumo の空き部屋。建物 (cassetteitem) + 号室を一行に flatten したもの。"""

    name: str                # マンション名
    area: str                # 区 (住所から抽出)
    address: str             # 住所
    access: str              # 駅情報 (複数行を " / " で join)
    age: str                 # 築年数 ("築17年")
    building_floors: str     # 構造/階数 ("地下1地上5階建")
    floor: str               # 所在階 ("5階")
    layout: str              # 間取り ("1LDK")
    floor_area: str          # 専有面積 ("70.13m²")
    rent: str                # 賃料 ("18.8万円")
    common_fee: str          # 管理費 ("-" もあり得る)
    jnc: str                 # 部屋 listing ID (URL の jnc_XXXXXXXXX)
    bc: str                  # 建物コード (URL の bc=XXXXXXXXX)
    detail_url: str          # 詳細ページの絶対 URL
    thumbnail_url: str | None = None

    @property
    def key(self) -> str:
        # Suumo は同じ部屋を仲介業者ごとに別 listing (jnc/bc) として並べるため、
        # listing ID ベースだと検索結果に重複が出るし、業者入れ替わりで
        # added/removed が振動する。物件フィンガープリント (建物名 + 階 +
        # 間取り + 面積 + 賃料) で同一視する。
        return (
            f"SUUMO:{self.name}|{self.floor}|{self.layout}"
            f"|{self.floor_area}|{self.rent}"
        )

    @property
    def building_key(self) -> str:
        return self.bc

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)
