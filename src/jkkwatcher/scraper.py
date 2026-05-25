from __future__ import annotations

import re
from typing import Iterable

import httpx
from bs4 import BeautifulSoup, Tag

from .models import KU_CODES, Area, Property


BASE = "https://jhomes.to-kousya.or.jp/search/jkknet/service"
INIT_URL = f"{BASE}/akiyaJyoukenStartInit"
SEARCH_URL = f"{BASE}/akiyaJyoukenRef"
CHANGE_COUNT_URL = f"{BASE}/AKIYAchangeCount"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

_XYZ_FOR_SEARCH_RE = re.compile(
    r'function\s+submitPage\b[^}]*?document\.akiSearch\.xyz\.value\s*=\s*"([A-F0-9]+)"',
    re.DOTALL,
)
_SEN_PAGE_RE = re.compile(r"senPage\('([^']*)','([^']*)','([^']*)','([^']*)'\)")


class JkkScraperError(RuntimeError):
    pass


class JkkScraper:
    def __init__(self, *, timeout: float = 30.0) -> None:
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

    def __enter__(self) -> "JkkScraper":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _get_search_form(self) -> BeautifulSoup:
        # Step 1: GET to acquire JSESSIONID
        self._client.get(INIT_URL)

        # Step 2: POST redirect=true to receive the real search form
        resp = self._client.post(
            INIT_URL,
            data={"redirect": "true", "url": INIT_URL},
            headers={"Referer": INIT_URL},
        )
        resp.encoding = "cp932"
        html = resp.text
        if "akiSearch" not in html:
            raise JkkScraperError("検索フォームを取得できませんでした")
        return BeautifulSoup(html, "lxml")

    @staticmethod
    def _extract_tokens(form_page: BeautifulSoup) -> dict[str, str]:
        form = form_page.find("form", attrs={"name": "akiSearch"})
        if not isinstance(form, Tag):
            raise JkkScraperError("akiSearch フォームが見つかりません")

        tokens: dict[str, str] = {}
        for field in ("token", "abcde", "sen_flg"):
            el = form.find("input", attrs={"name": field})
            if isinstance(el, Tag):
                tokens[field] = el.get("value", "") or ""

        scripts = "\n".join(s.get_text() for s in form_page.find_all("script"))
        m = _XYZ_FOR_SEARCH_RE.search(scripts)
        if not m:
            raise JkkScraperError("検索用 xyz トークンを抽出できませんでした")
        tokens["jklm"] = m.group(1)
        return tokens

    def search(
        self,
        *,
        ku_codes: Iterable[str] | None = None,
        area: Area = Area.KU,
    ) -> list[Property]:
        form_page = self._get_search_form()
        tokens = self._extract_tokens(form_page)

        codes = list(ku_codes) if ku_codes else list(KU_CODES.keys())

        data: dict[str, str | list[str]] = {
            "token": tokens["token"],
            "abcde": tokens["abcde"],
            "jklm": tokens["jklm"],
            "sen_flg": tokens.get("sen_flg", "1"),
            "akiyaInitRM.akiyaRefM.allCheck": (
                "ALLKU" if area is Area.KU else "ALLSI"
            ),
            "akiyaInitRM.akiyaRefM.checks": codes,
            # 検索条件のデフォルト値（全件取得）
            "akiyaInitRM.akiyaRefM.requiredTime": "99",
            "akiyaInitRM.akiyaRefM.bus": "1",
            "akiyaInitRM.akiyaRefM.yachinFrom": "0",
            "akiyaInitRM.akiyaRefM.yachinTo": "999999999",
            "akiyaInitRM.akiyaRefM.mensekiFrom": "0",
            "akiyaInitRM.akiyaRefM.mensekiTo": "9999.99",
            "akiyaInitRM.akiyaRefM.chikuNensu": "99",
        }

        resp = self._client.post(
            SEARCH_URL,
            data=data,
            headers={"Referer": INIT_URL},
        )
        resp.encoding = "cp932"
        return self._parse_results(resp.text)

    @staticmethod
    def _parse_results(html: str) -> list[Property]:
        soup = BeautifulSoup(html, "lxml")

        result_table: Tag | None = None
        for table in soup.find_all("table", class_="cell666666"):
            if "住宅名" in table.get_text():
                result_table = table
                break
        if result_table is None:
            return []

        properties: list[Property] = []
        rows = result_table.find_all("tr")
        for row in rows[1:]:  # skip header
            cells = row.find_all("td")
            if len(cells) < 11:
                continue

            sen = _SEN_PAGE_RE.search(str(cells[10]))
            if not sen:
                continue

            img_tag = cells[0].find("img")
            thumb = img_tag.get("src") if isinstance(img_tag, Tag) else None

            properties.append(
                Property(
                    name=cells[1].get_text(strip=True),
                    area=cells[2].get_text(strip=True),
                    priority_type=cells[3].get_text(strip=True),
                    house_type=cells[4].get_text(strip=True),
                    layout=cells[5].get_text(strip=True),
                    floor_area=cells[6].get_text(strip=True),
                    rent=cells[7].get_text(strip=True),
                    common_fee=cells[8].get_text(strip=True),
                    units=cells[9].get_text(strip=True),
                    room_id=sen.group(2),
                    residence_code=sen.group(3),
                    thumbnail_url=thumb,
                )
            )
        return properties
