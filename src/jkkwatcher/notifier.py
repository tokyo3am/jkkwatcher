from __future__ import annotations

from typing import Any

import httpx

from .diff import Diff
from .models import Property
from .scraper import INIT_URL

# Slack のメッセージは最大 50 ブロック。物件カードが詰めても収まるよう、
# 差分カードと現状リストの上限をそれぞれ別途絞る。
MAX_DETAIL_CARDS = 8
MAX_CURRENT_ROWS = 30


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


def _truncated_cards(
    label_emoji: str, label: str, items: list[Property]
) -> list[dict[str, Any]]:
    if not items:
        return []
    header = {
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"{label_emoji} {len(items)} {label}",
        },
    }
    blocks: list[dict[str, Any]] = [header, {"type": "divider"}]
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


def _current_summary(current: list[Property]) -> list[dict[str, Any]]:
    header = {
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f":clipboard: 現在の空室状況 {len(current)} 件",
        },
    }
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


def build_payload(
    diff: Diff,
    current: list[Property],
    *,
    context_text: str | None = None,
) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    blocks.extend(_truncated_cards(":white_check_mark:", "件の新着物件があります", diff.added))
    blocks.extend(_truncated_cards(":x:", "件の物件が申し込まれました", diff.removed))
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
        f"JKK: 新着 {len(diff.added)}件 / 終了 {len(diff.removed)}件"
        f" / 現在 {len(current)}件"
    )
    return {"text": summary, "blocks": blocks}


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
