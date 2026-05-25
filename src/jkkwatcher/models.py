from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


KU_CODES: dict[str, str] = {
    "01": "千代田区", "02": "中央区", "03": "港区", "04": "新宿区",
    "05": "文京区", "06": "台東区", "07": "墨田区", "08": "江東区",
    "09": "品川区", "10": "目黒区", "11": "大田区", "12": "世田谷区",
    "13": "渋谷区", "14": "中野区", "15": "杉並区", "16": "豊島区",
    "17": "北区", "18": "荒川区", "19": "板橋区", "20": "練馬区",
    "21": "足立区", "22": "葛飾区", "23": "江戸川区",
}


class Area(str, Enum):
    KU = "ku"
    SHI = "shi"


@dataclass(frozen=True, slots=True)
class Property:
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

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)
