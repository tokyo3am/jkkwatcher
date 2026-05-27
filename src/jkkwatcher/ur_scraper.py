from __future__ import annotations

import html
import re
from typing import Any, Iterable

import httpx
from bs4 import BeautifulSoup

from .models import SKCS_CODES, UrProperty


# UR の chintai サイト本体 (Referer / 詳細ページ URL のオリジン)
SITE_ORIGIN = "https://www.ur-net.go.jp"

# 検索結果ページ。Referer に使う以外、直接叩く必要はない。
RESULT_PAGE = f"{SITE_ORIGIN}/chintai/kanto/tokyo/result/"

# AJAX エンドポイント。.NET MVC の Web API で、form-urlencoded を受ける。
API_BASE = "https://chintai.r6.ur-net.go.jp/chintai/api"
BUKKEN_URL = f"{API_BASE}/bukken/result/bukken_result/"
ROOM_URL = f"{API_BASE}/bukken/result/bukken_result_room/"

# 東京 (tdfk=13) は kanto ブロック固定。
BLOCK = "kanto"
TDFK = "13"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


class UrScraperError(RuntimeError):
    pass


# "中央区晴海3-6-8" → "中央区" / "千代田区一番町1" → "千代田区"。
# 23 区以外 (市町村) もそのまま拾うために緩めの正規表現にしている。
_KU_RE = re.compile(r"^(\S+?[区市町村])")


def _extract_ku(address: str) -> str:
    m = _KU_RE.match(address.strip())
    return m.group(1) if m else ""


def _strip_html(html: str) -> str:
    """traffic フィールド (<li>...</li> の連結) からテキストを取り出す。"""
    if not html:
        return ""
    text = BeautifulSoup(html, "lxml").get_text(separator=" / ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _detail_url(room_link_pc: str | None) -> str:
    if not room_link_pc:
        return ""
    if room_link_pc.startswith("http"):
        return room_link_pc
    return SITE_ORIGIN + room_link_pc


class UrScraper:
    def __init__(self, *, timeout: float = 30.0, page_size: int = 100) -> None:
        self._page_size = page_size
        self._client = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "ja,en;q=0.9",
                "Origin": SITE_ORIGIN,
                "Referer": RESULT_PAGE,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "UrScraper":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _base_params(self, *, skcs: str) -> dict[str, str]:
        # ブラウザがそのまま送っているフィールド一式 (空文字を含む)。
        # API は欠けたフィールドがあると null を返すので、最小セットを揃える。
        return {
            "rent_low": "",
            "rent_high": "",
            "walk": "",
            "floorspace_low": "",
            "floorspace_high": "",
            "years": "",
            "mode": "area",
            "skcs": skcs,
            "block": BLOCK,
            "tdfk": TDFK,
            "rireki_tdfk": TDFK,
            "orderByField": "1",
            "pageSize": str(self._page_size),
            "pageIndex": "0",
            "shisya": "",
            "danchi": "",
            "shikibetu": "",
            "pageIndexRoom": "0",
            "sp": "",
        }

    def _post_json(self, url: str, data: dict[str, str]) -> Any:
        resp = self._client.post(url, data=data)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError as e:
            raise UrScraperError(
                f"UR API のレスポンスが JSON でない: url={url} body={resp.text[:200]!r}"
            ) from e

    def _fetch_bukken(self, *, skcs: str) -> list[dict[str, Any]]:
        """指定 skcs の建物 (bukken) 一覧をページ送りしながら全件取得。"""
        results: list[dict[str, Any]] = []
        page = 0
        while True:
            params = self._base_params(skcs=skcs)
            params["pageIndex"] = str(page)
            data = self._post_json(BUKKEN_URL, params)
            if not data:  # null や [] で終端
                break
            if not isinstance(data, list):
                raise UrScraperError(
                    f"UR bukken_result が想定外の型: {type(data).__name__}"
                )
            results.extend(data)

            # allCount は実際の総件数。pageMax はサーバ側のデフォルト pageSize
            # (=10) を仮定した値なので、pageSize を大きくしている我々の用途では
            # 当てにできない。
            try:
                all_count = int(data[0].get("allCount") or 0)
            except (TypeError, ValueError):
                all_count = 0
            if all_count <= 0 or len(results) >= all_count:
                break
            # サーバが返したデータ件数が 0 件だと無限ループするので保険。
            if len(data) == 0:
                break
            page += 1
        return results

    def _fetch_rooms(
        self, *, skcs: str, shisya: str, danchi: str, shikibetu: str
    ) -> list[dict[str, Any]]:
        """1 建物分の部屋 (room) をページ送りしながら全件取得。"""
        rooms: list[dict[str, Any]] = []
        page = 0
        while True:
            params = self._base_params(skcs=skcs)
            params["shisya"] = shisya
            params["danchi"] = danchi
            params["shikibetu"] = shikibetu
            params["pageIndexRoom"] = str(page)
            data = self._post_json(ROOM_URL, params)
            if not data:
                break
            if not isinstance(data, list):
                raise UrScraperError(
                    f"UR bukken_result_room が想定外の型: {type(data).__name__}"
                )
            rooms.extend(data)

            first = data[0]
            try:
                all_count = int(first.get("allCount") or 0)
                row_max = int(first.get("rowMax") or 0)
            except (TypeError, ValueError):
                all_count, row_max = len(rooms), len(data)
            page += 1
            if row_max <= 0 or len(rooms) >= all_count:
                break
        return rooms

    def search(self, *, skcs_codes: Iterable[str] | None = None) -> list[UrProperty]:
        codes = list(skcs_codes) if skcs_codes else list(SKCS_CODES.keys())
        properties: list[UrProperty] = []
        seen_keys: set[str] = set()

        for skcs in codes:
            bukken_list = self._fetch_bukken(skcs=skcs)
            for bukken in bukken_list:
                try:
                    room_count = int(bukken.get("roomCount") or 0)
                except (TypeError, ValueError):
                    room_count = 0
                if room_count <= 0:
                    continue

                shisya = bukken.get("shisya") or ""
                danchi = bukken.get("danchi") or ""
                shikibetu = bukken.get("shikibetu") or ""
                if not (shisya and danchi):
                    continue

                rooms = self._fetch_rooms(
                    skcs=skcs,
                    shisya=shisya,
                    danchi=danchi,
                    shikibetu=shikibetu,
                )

                address = bukken.get("place") or ""
                access = _strip_html(bukken.get("traffic") or "")
                name = bukken.get("danchiNm") or ""
                ku = _extract_ku(address)

                for room in rooms:
                    room_id = room.get("id") or ""
                    if not room_id:
                        continue
                    rent = html.unescape((room.get("rent") or "").strip())
                    common_fee = html.unescape(
                        (room.get("commonfee") or "").strip()
                    )
                    # floorspace は "60&#13217;" のように HTML エンティティ込み。
                    floor_area = html.unescape(
                        (room.get("floorspace") or "").strip()
                    )
                    floor = (room.get("floor") or "").strip()
                    layout = (room.get("type") or "").strip()
                    # 部屋エンドポイントは roomNo を持たず、roomNmSub ("3712号室")
                    # が表示名に該当する。roomNmMain は基本空。
                    room_no = (
                        (room.get("roomNmSub") or room.get("roomNmMain") or "")
                        .strip()
                    )
                    thumb = room.get("roomImg") or bukken.get("bukkenImg")
                    detail = _detail_url(room.get("roomLinkPc"))

                    prop = UrProperty(
                        name=name,
                        area=ku,
                        address=address,
                        access=access,
                        layout=layout,
                        floor_area=floor_area,
                        floor=floor,
                        rent=rent,
                        common_fee=common_fee,
                        shisya=shisya,
                        danchi=danchi,
                        shikibetu=shikibetu,
                        room_id=room_id,
                        room_no=room_no,
                        detail_url=detail,
                        thumbnail_url=thumb,
                    )
                    # 複数 skcs に同じ建物が引っかかる重複を除外。
                    if prop.key in seen_keys:
                        continue
                    seen_keys.add(prop.key)
                    properties.append(prop)

        return properties
