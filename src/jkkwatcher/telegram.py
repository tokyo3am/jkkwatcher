"""Telegram Bot API への通知 (watchlist ヒットのみ・プレーンテキスト)。

Slack (notifier.py) が一次チャンネルで、新着/終了/現在の空室一覧までリッチに出す。
こちらは「ウォッチ中の物件に空きが出た」ことだけを確実に手元へ届ける二次チャンネル
なので、Block Kit 相当のレイアウトは持たない。路線ロゴ (Slack カスタム絵文字) も
Telegram では `:keio:` という文字列にしかならないため、駅・路線情報は載せない。
"""
from __future__ import annotations

import html
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from .models import Property, SuumoProperty, UrProperty
from .watchlist import Source

API_BASE = "https://api.telegram.org"

# sendMessage の text 上限は 4096 char。物件ブロック境界で切るため余裕を持たせる。
MESSAGE_SAFE_LIMIT = 3900

_LABELS: dict[Source, str] = {"jkk": "JKK", "ur": "UR", "suumo": "Suumo"}


# ---------- テキスト整形 ----------


def _esc(text: str) -> str:
    """Telegram の HTML parse_mode 用エスケープ (未エスケープの `&` で 400 になる)。"""
    return html.escape(text or "", quote=False)


def _title(name: str, detail_url: str = "") -> str:
    body = f"<b>{_esc(name)}</b>"
    if not detail_url:
        return body
    return f'<a href="{html.escape(detail_url, quote=True)}">{body}</a>'


def _jkk_line(p: Property) -> str:
    # JKK には物件詳細ページの URL が無いためリンクを張れない。
    return (
        f"{_title(p.name)}\n"
        f"{_esc(p.area)} / {_esc(p.layout)} / {_esc(p.floor_area)}m² / "
        f"{_esc(p.rent)}円 / {_esc(p.units)}戸"
    )


def _ur_line(p: UrProperty) -> str:
    return (
        f"{_title(p.name, p.detail_url)}\n"
        f"{_esc(p.area)} / {_esc(p.room_no)} / {_esc(p.layout)} / "
        f"{_esc(p.floor_area)} / {_esc(p.rent)}"
    )


def _suumo_line(p: SuumoProperty) -> str:
    return (
        f"{_title(p.name, p.detail_url)}\n"
        f"{_esc(p.area)} / {_esc(p.layout)} / {_esc(p.floor_area)} / "
        f"{_esc(p.floor)} / {_esc(p.rent)}"
    )


_LINE_FORMATTERS: dict[Source, Callable[[Any], str]] = {
    "jkk": _jkk_line,
    "ur": _ur_line,
    "suumo": _suumo_line,
}


def _pack(header: str, blocks: list[str]) -> list[str]:
    """header を各メッセージの先頭に付けつつ、上限内に物件ブロックを詰める。

    ヒットは切り捨てない (Slack と違い件数で打ち切らない)。1 ブロック単独で上限を
    越える場合はそのまま 1 通にする。
    """
    groups: list[list[str]] = []
    cur: list[str] = []
    cur_size = len(header)
    for block in blocks:
        addition = len(block) + 2  # ブロック間の "\n\n"
        if cur and cur_size + addition > MESSAGE_SAFE_LIMIT:
            groups.append(cur)
            cur, cur_size = [block], len(header) + addition
        else:
            cur.append(block)
            cur_size += addition
    if cur:
        groups.append(cur)

    total = len(groups)
    return [
        "\n\n".join([header if total == 1 else f"{header} ({i + 1}/{total})", *group])
        for i, group in enumerate(groups)
    ]


def build_hit_messages(source: Source, hits: Sequence[Any]) -> list[str]:
    """ウォッチ一致物件を 1 通以上の HTML テキストにする。ヒット無しなら空リスト。"""
    if not hits:
        return []
    formatter = _LINE_FORMATTERS[source]
    header = (
        f"🔔 <b>[{_LABELS[source]}] ウォッチ中の物件に空きが出ました "
        f"({len(hits)} 件)</b>"
    )
    return _pack(header, [formatter(p) for p in hits])


# ---------- 送信 ----------


def _is_ok(resp: httpx.Response) -> bool:
    try:
        return resp.json().get("ok") is True
    except ValueError:
        return False


def notify(
    token: str,
    chat_id: str,
    texts: Sequence[str],
    *,
    timeout: float = 10.0,
) -> None:
    """テキストを順に送信。途中でエラーが出たら raise (以降は送られない)。

    bot token は URL パスに入るため、例外に URL を載せない。httpx の例外も
    `from None` で握り直す (traceback ごと GitHub Actions のログに token が
    残るのを避ける)。Telegram のエラーレスポンス body に token は含まれない。
    """
    url = f"{API_BASE}/bot{token}/sendMessage"
    for i, text in enumerate(texts):
        try:
            resp = httpx.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "link_preview_options": {"is_disabled": True},
                },
                timeout=timeout,
            )
        except httpx.HTTPError as e:
            raise RuntimeError(
                f"Telegram sendMessage failed (message {i + 1}/{len(texts)}): "
                f"{type(e).__name__}"
            ) from None
        if resp.status_code != 200 or not _is_ok(resp):
            raise RuntimeError(
                f"Telegram sendMessage failed (message {i + 1}/{len(texts)}): "
                f"status={resp.status_code} body={resp.text!r}"
            )
