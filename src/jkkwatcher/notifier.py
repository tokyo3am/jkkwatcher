from __future__ import annotations

from typing import Any

import httpx

from .diff import Diff
from .models import Property, SuumoProperty, UrProperty
from .scraper import INIT_URL
from .suumo_scraper import DEFAULT_SEARCH_URL as SUUMO_SEARCH_URL

# Slack のメッセージは最大 50 ブロック。物件カードが詰めても収まるよう、
# 差分カードと現状リストの上限をそれぞれ別途絞る。
MAX_DETAIL_CARDS = 8
MAX_CURRENT_ROWS = 30

# UR 検索結果ページ (フッターからリンクする用)
UR_SEARCH_URL = (
    "https://www.ur-net.go.jp/chintai/kanto/tokyo/result/"
    "?area=01&skcs=102&area=02&skcs=108&skcs=121&skcs=122&skcs=118"
    "&area=03&skcs=109&skcs=110&skcs=112&area=04&skcs=120&skcs=115"
    "&area=05&skcs=119&tdfk=13&todofuken=tokyo"
)

# Slack の Incoming Webhook で表示する送信者名・アイコン。
# 同じチャンネルに JKK と UR の通知が並んだとき、見た目で識別できるよう
# 別の bot として表示させる。
# (Slack 側で webhook を作ったアプリに chat:write.customize スコープが
#  無い場合、これらは無視される。その場合はワークスペース管理者が
#  アプリの設定を見直すか、2 つの webhook URL を使い分ける必要がある。)
JKK_BOT_USERNAME = "JKK Watcher"
JKK_BOT_ICON_EMOJI = ":classical_building:"  # 🏛️
UR_BOT_USERNAME = "UR Watcher"
UR_BOT_ICON_EMOJI = ":office:"  # 🏢
SUUMO_BOT_USERNAME = "Suumo Watcher"
SUUMO_BOT_ICON_EMOJI = ":house:"  # 🏠


def _header(prefix: str, emoji: str, text: str) -> dict[str, Any]:
    """header block の生成。prefix で [JKK] / [UR] を付ける。"""
    return {
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"{prefix} {emoji} {text}",
            "emoji": True,
        },
    }


def _property_card(prop: Property) -> list[dict[str, Any]]:
    section: dict[str, Any] = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*{prop.name}*\n{prop.area}・{prop.house_type}・{prop.priority_type}",
        },
        "fields": [
            {"type": "mrkdwn", "text": f"*間取り*\n{prop.layout}"},
            {"type": "mrkdwn", "text": f"*戸数*\n{prop.units}"},
            {"type": "mrkdwn", "text": f"*床面積*\n{prop.floor_area} m²"},
            {"type": "mrkdwn", "text": f"*家賃*\n{prop.rent} 円"},
        ],
    }
    if prop.thumbnail_url:
        section["accessory"] = {
            "type": "image",
            "image_url": prop.thumbnail_url,
            "alt_text": prop.name,
        }
    return [section]


def _ur_property_card(prop: UrProperty) -> list[dict[str, Any]]:
    # 物件名は詳細ページへのリンクにする (UR は room_id 単位で URL が引ける)。
    name_md = f"<{prop.detail_url}|{prop.name}>" if prop.detail_url else prop.name
    section: dict[str, Any] = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*{name_md}*\n{prop.area}・{prop.room_no}・{prop.access}",
        },
        "fields": [
            {"type": "mrkdwn", "text": f"*間取り*\n{prop.layout}"},
            {"type": "mrkdwn", "text": f"*階*\n{prop.floor}"},
            {"type": "mrkdwn", "text": f"*床面積*\n{prop.floor_area}"},
            {"type": "mrkdwn", "text": f"*家賃*\n{prop.rent}"},
        ],
    }
    if prop.thumbnail_url:
        section["accessory"] = {
            "type": "image",
            "image_url": prop.thumbnail_url,
            "alt_text": prop.name,
        }
    return [section]


def _truncated_cards(
    prefix: str, label_emoji: str, label: str, items: list[Property]
) -> list[dict[str, Any]]:
    if not items:
        return []
    blocks: list[dict[str, Any]] = [
        _header(prefix, label_emoji, f"{len(items)} {label}"),
        {"type": "divider"},
    ]
    for prop in items[:MAX_DETAIL_CARDS]:
        blocks.extend(_property_card(prop))
        blocks.append({"type": "divider"})
    if len(items) > MAX_DETAIL_CARDS:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"_他 {len(items) - MAX_DETAIL_CARDS} 件は省略_",
                },
            }
        )
    return blocks


def _ur_truncated_cards(
    prefix: str, label_emoji: str, label: str, items: list[UrProperty]
) -> list[dict[str, Any]]:
    if not items:
        return []
    blocks: list[dict[str, Any]] = [
        _header(prefix, label_emoji, f"{len(items)} {label}"),
        {"type": "divider"},
    ]
    for prop in items[:MAX_DETAIL_CARDS]:
        blocks.extend(_ur_property_card(prop))
        blocks.append({"type": "divider"})
    if len(items) > MAX_DETAIL_CARDS:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"_他 {len(items) - MAX_DETAIL_CARDS} 件は省略_",
                },
            }
        )
    return blocks


def _current_summary(current: list[Property]) -> list[dict[str, Any]]:
    header = _header("[JKK]", ":clipboard:", f"現在の空室状況 {len(current)} 件")
    if not current:
        return [
            header,
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "_該当物件なし_"},
            },
        ]

    lines = [
        f"• *{p.name}* ({p.area}) {p.layout} / {p.floor_area} m² / {p.rent} 円 / {p.units} 戸"
        for p in current[:MAX_CURRENT_ROWS]
    ]
    if len(current) > MAX_CURRENT_ROWS:
        lines.append(f"_…他 {len(current) - MAX_CURRENT_ROWS} 件_")
    return [
        header,
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
    ]


def _ur_current_summary(current: list[UrProperty]) -> list[dict[str, Any]]:
    header = _header("[UR]", ":clipboard:", f"現在の空室状況 {len(current)} 件")
    if not current:
        return [
            header,
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "_該当物件なし_"},
            },
        ]

    lines = []
    for p in current[:MAX_CURRENT_ROWS]:
        name_md = f"<{p.detail_url}|{p.name}>" if p.detail_url else p.name
        lines.append(
            f"• *{name_md}* ({p.area}) {p.room_no} / {p.layout} / "
            f"{p.floor_area} / {p.rent}"
        )
    if len(current) > MAX_CURRENT_ROWS:
        lines.append(f"_…他 {len(current) - MAX_CURRENT_ROWS} 件_")
    return [
        header,
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
    ]


def build_payload(
    diff: Diff[Property],
    current: list[Property],
    *,
    context_text: str | None = None,
) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    blocks.extend(
        _truncated_cards(
            "[JKK]", ":white_check_mark:", "件の新着物件があります", diff.added
        )
    )
    blocks.extend(
        _truncated_cards(
            "[JKK]", ":x:", "件の物件が申し込まれました", diff.removed
        )
    )
    blocks.extend(_current_summary(current))

    footer_text = context_text or (
        f"Triggered by <https://github.com/tokyo3am/jkkwatcher|jkkwatcher>"
        f" · <{INIT_URL}|JKK で見る>"
    )
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": footer_text}],
        }
    )

    summary = (
        f"[JKK] 新着 {len(diff.added)}件 / 終了 {len(diff.removed)}件"
        f" / 現在 {len(current)}件"
    )
    return {
        "username": JKK_BOT_USERNAME,
        "icon_emoji": JKK_BOT_ICON_EMOJI,
        "text": summary,
        "blocks": blocks,
    }


def build_ur_payload(
    diff: Diff[UrProperty],
    current: list[UrProperty],
    *,
    context_text: str | None = None,
) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    blocks.extend(
        _ur_truncated_cards(
            "[UR]", ":white_check_mark:", "件の新着物件があります", diff.added
        )
    )
    blocks.extend(
        _ur_truncated_cards(
            "[UR]", ":x:", "件の物件が申し込まれました", diff.removed
        )
    )
    blocks.extend(_ur_current_summary(current))

    footer_text = context_text or (
        f"Triggered by <https://github.com/tokyo3am/jkkwatcher|jkkwatcher>"
        f" · <{UR_SEARCH_URL}|UR で見る>"
    )
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": footer_text}],
        }
    )

    summary = (
        f"[UR] 新着 {len(diff.added)}件 / 終了 {len(diff.removed)}件"
        f" / 現在 {len(current)}件"
    )
    return {
        "username": UR_BOT_USERNAME,
        "icon_emoji": UR_BOT_ICON_EMOJI,
        "text": summary,
        "blocks": blocks,
    }


def _suumo_property_card(prop: SuumoProperty) -> list[dict[str, Any]]:
    name_md = f"<{prop.detail_url}|{prop.name}>" if prop.detail_url else prop.name
    section: dict[str, Any] = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*{name_md}*\n{prop.area}・{prop.age}・{prop.access}",
        },
        "fields": [
            {"type": "mrkdwn", "text": f"*間取り*\n{prop.layout}"},
            {"type": "mrkdwn", "text": f"*階*\n{prop.floor}"},
            {"type": "mrkdwn", "text": f"*床面積*\n{prop.floor_area}"},
            {"type": "mrkdwn", "text": f"*家賃*\n{prop.rent}"},
        ],
    }
    if prop.thumbnail_url:
        section["accessory"] = {
            "type": "image",
            "image_url": prop.thumbnail_url,
            "alt_text": prop.name,
        }
    return [section]


def _suumo_truncated_cards(
    prefix: str, label_emoji: str, label: str, items: list[SuumoProperty]
) -> list[dict[str, Any]]:
    if not items:
        return []
    blocks: list[dict[str, Any]] = [
        _header(prefix, label_emoji, f"{len(items)} {label}"),
        {"type": "divider"},
    ]
    for prop in items[:MAX_DETAIL_CARDS]:
        blocks.extend(_suumo_property_card(prop))
        blocks.append({"type": "divider"})
    if len(items) > MAX_DETAIL_CARDS:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"_他 {len(items) - MAX_DETAIL_CARDS} 件は省略_",
                },
            }
        )
    return blocks


def _suumo_current_summary(current: list[SuumoProperty]) -> list[dict[str, Any]]:
    header = _header("[Suumo]", ":clipboard:", f"現在の空室状況 {len(current)} 件")
    if not current:
        return [
            header,
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "_該当物件なし_"},
            },
        ]

    lines = []
    for p in current[:MAX_CURRENT_ROWS]:
        name_md = f"<{p.detail_url}|{p.name}>" if p.detail_url else p.name
        lines.append(
            f"• *{name_md}* ({p.area}) {p.floor} / {p.layout} / "
            f"{p.floor_area} / {p.rent}"
        )
    if len(current) > MAX_CURRENT_ROWS:
        lines.append(f"_…他 {len(current) - MAX_CURRENT_ROWS} 件_")
    return [
        header,
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
    ]


def build_suumo_payload(
    diff: Diff[SuumoProperty],
    current: list[SuumoProperty],
    *,
    context_text: str | None = None,
) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    blocks.extend(
        _suumo_truncated_cards(
            "[Suumo]", ":white_check_mark:", "件の新着物件があります", diff.added
        )
    )
    blocks.extend(
        _suumo_truncated_cards(
            "[Suumo]", ":x:", "件の物件が掲載終了しました", diff.removed
        )
    )
    blocks.extend(_suumo_current_summary(current))

    footer_text = context_text or (
        f"Triggered by <https://github.com/tokyo3am/jkkwatcher|jkkwatcher>"
        f" · <{SUUMO_SEARCH_URL}|Suumo で見る>"
    )
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": footer_text}],
        }
    )

    summary = (
        f"[Suumo] 新着 {len(diff.added)}件 / 終了 {len(diff.removed)}件"
        f" / 現在 {len(current)}件"
    )
    return {
        "username": SUUMO_BOT_USERNAME,
        "icon_emoji": SUUMO_BOT_ICON_EMOJI,
        "text": summary,
        "blocks": blocks,
    }


def notify(webhook_url: str, payload: dict[str, Any], *, timeout: float = 10.0) -> None:
    resp = httpx.post(
        webhook_url,
        json=payload,
        timeout=timeout,
        headers={"Content-Type": "application/json"},
    )
    if resp.status_code != 200 or resp.text.strip() != "ok":
        raise RuntimeError(
            f"Slack webhook POST failed: status={resp.status_code} body={resp.text!r}"
        )
