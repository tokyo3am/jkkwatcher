from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup, Tag

from .models import SuumoProperty


SITE_ORIGIN = "https://suumo.jp"

# `--url` で実行時に上書き可能。
DEFAULT_SEARCH_URL = (
    "https://suumo.jp/jj/chintai/ichiran/FR301FC001/"
    "?ar=030&bs=040&pc=50&smk=&po1=16&po2=99&kz=1"
    "&tc=0401303&tc=0400302&tc=0400205&tc=0400905&tc=0401002"
    "&shkr1=03&shkr2=03&shkr3=03&shkr4=03"
    "&ekInput=17640"
    "&rn=0350&rn=0370&rn=0395&rn=0205&rn=0230&rn=0240&rn=0265&rn=0275&rn=0280&rn=0305"
    "&rn=0005&rn=0010&rn=0015&rn=0025&rn=0030&rn=0035&rn=0040&rn=0043&rn=0045&rn=0050"
    "&rn=0060&rn=0065&rn=0070&rn=0573&rn=7580&rn=7585"
    "&ta=13&kskbn=01&tj=20&nk=0&cb=0.0&ct=20.0&co=1&ts=1&et=10&mb=40&cn=9999999"
    "&tc=0400301&tc=0400101&fw2="
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

# Suumo の pagination は URL クエリの `&page=N` を採用 (1-indexed)。
_PAGE_PARAM = "page"

# ループ防止: 一覧ページが現実的に取り得る上限。
_MAX_PAGES = 50

_KU_RE = re.compile(r"^(\S+?[都道府県])?(\S+?[区市町村])")
_DETAIL_RE = re.compile(r"(/chintai/jnc_\d+/)")
_BC_RE = re.compile(r"[?&]bc=(\d+)")
_JNC_RE = re.compile(r"jnc_(\d+)")


class SuumoScraperError(RuntimeError):
    pass


def _extract_ku(address: str) -> str:
    """住所文字列から「○○区」「○○市」を抽出。"""
    m = _KU_RE.match(address.strip())
    return m.group(2) if m else ""


def _li_texts(td: Tag) -> list[str]:
    """セル内の <li> をテキスト化して返す。"""
    return [li.get_text(" ", strip=True) for li in td.find_all("li")]


def _clean_floor_area(text: str) -> str:
    """`70.13m 2` (sup タグが分離した状態) → `70.13m²` に正規化。"""
    # `m 2` / `m 2` / `m  2` を `m²` に。
    return re.sub(r"m\s*2$", "m²", text).strip()


def _img_url(img: Tag | None) -> str | None:
    """Suumo の遅延読み込み画像。`rel` 属性に実 URL が入っている。"""
    if img is None:
        return None
    for attr in ("data-src", "rel", "data-original", "src"):
        v = img.get(attr)
        if v and not v.startswith("data:"):
            # Slack の image_url は絶対 URL 必須。プレースホルダは
            # `/edit/assets/...` のような相対 URL で返るため絶対化する。
            return _absolute(v)
    return None


def _add_page_param(url: str, page: int) -> str:
    """URL に page クエリを追加 (1-indexed)。既存の page= があれば置換。"""
    parsed = urlparse(url)
    query = parsed.query
    # `page=...` を除去
    query = re.sub(r"(?:^|&)" + _PAGE_PARAM + r"=[^&]*", "", query)
    query = query.lstrip("&")
    sep = "&" if query else ""
    new_query = f"{query}{sep}{_PAGE_PARAM}={page}"
    return urlunparse(parsed._replace(query=new_query))


def _absolute(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    return SITE_ORIGIN + href


class SuumoScraper:
    def __init__(
        self,
        *,
        search_url: str = DEFAULT_SEARCH_URL,
        timeout: float = 30.0,
    ) -> None:
        self._search_url = search_url
        self._client = httpx.Client(
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "ja,en;q=0.9",
            },
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SuumoScraper":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _fetch_page(self, page: int) -> BeautifulSoup:
        url = _add_page_param(self._search_url, page)
        resp = self._client.get(url)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")

    def search(self) -> list[SuumoProperty]:
        properties: list[SuumoProperty] = []
        seen_keys: set[str] = set()

        for page in range(1, _MAX_PAGES + 1):
            soup = self._fetch_page(page)
            cassettes = soup.find_all("div", class_="cassetteitem")
            if not cassettes:
                break

            new_in_page = 0
            for cassette in cassettes:
                for prop in _parse_cassette(cassette):
                    if prop.key in seen_keys:
                        continue
                    seen_keys.add(prop.key)
                    properties.append(prop)
                    new_in_page += 1

            # 全件 dedupe で何も増えなければ終端。
            if new_in_page == 0:
                break

        return properties


def _parse_cassette(cassette: Tag) -> list[SuumoProperty]:
    """1 建物 (cassetteitem) を 0 件以上の SuumoProperty に展開。"""
    title_el = cassette.select_one(".cassetteitem_content-title")
    if title_el is None:
        return []
    name = title_el.get_text(strip=True)

    addr_el = cassette.select_one(".cassetteitem_detail-col1")
    address = addr_el.get_text(strip=True) if addr_el else ""
    area = _extract_ku(address)

    access_lines = [
        a.get_text(strip=True)
        for a in cassette.select(".cassetteitem_detail-col2 .cassetteitem_detail-text")
    ]
    access = " / ".join(filter(None, access_lines))

    col3 = [c.get_text(strip=True) for c in cassette.select(".cassetteitem_detail-col3 div")]
    age = col3[0] if len(col3) > 0 else ""
    building_floors = col3[1] if len(col3) > 1 else ""

    building_img = cassette.select_one(".cassetteitem_object-item img")
    building_thumb = _img_url(building_img)

    rooms: list[SuumoProperty] = []
    for tbody in cassette.select("table.cassetteitem_other tbody"):
        tds = tbody.find_all("td")
        if len(tds) < 9:
            continue

        floor = tds[2].get_text(" ", strip=True)

        rent_fee = _li_texts(tds[3])
        rent = rent_fee[0] if rent_fee else ""
        common_fee = rent_fee[1] if len(rent_fee) > 1 else ""

        layout_area = _li_texts(tds[5])
        layout = layout_area[0] if layout_area else ""
        floor_area = (
            _clean_floor_area(layout_area[1]) if len(layout_area) > 1 else ""
        )

        # 部屋ごとの個別画像はないので建物サムネを流用。
        thumb = building_thumb

        detail_a = tbody.find("a", class_=lambda c: c and "js-cassette_link_href" in c)
        href = detail_a.get("href") if isinstance(detail_a, Tag) else ""
        if not href:
            continue

        jnc_m = _JNC_RE.search(href)
        bc_m = _BC_RE.search(href)
        if not jnc_m or not bc_m:
            continue
        jnc = jnc_m.group(1)
        bc = bc_m.group(1)

        # 詳細 URL は jnc 部分だけで成立。bc クエリは付けて返す。
        detail_path_m = _DETAIL_RE.search(href)
        detail_path = detail_path_m.group(1) if detail_path_m else f"/chintai/jnc_{jnc}/"
        detail_url = _absolute(f"{detail_path}?bc={bc}")

        rooms.append(
            SuumoProperty(
                name=name,
                area=area,
                address=address,
                access=access,
                age=age,
                building_floors=building_floors,
                floor=floor,
                layout=layout,
                floor_area=floor_area,
                rent=rent,
                common_fee=common_fee,
                jnc=jnc,
                bc=bc,
                detail_url=detail_url,
                thumbnail_url=thumb,
            )
        )

    return rooms
