from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

import httpx

from .diff import Diff
from .line_emoji import route_emoji
from .models import Property, PropertyLike, SuumoProperty, UrProperty
from .scraper import INIT_URL
from .suumo_scraper import DEFAULT_SEARCH_URL as SUUMO_SEARCH_URL
from .watchlist import NotifyConfig, Source

P = TypeVar("P", bound=PropertyLike)

# Slack の上限 (Block Kit ドキュメント由来)
SLACK_MAX_BLOCKS = 50
BLOCKS_SAFE_LIMIT = 45  # 余裕 5
SECTION_TEXT_LIMIT = 3000
SECTION_TEXT_SAFE_LIMIT = 2900  # 余裕 100 (改行・末尾装飾を考慮)

# 新着/終了/ウォッチ一致セクションでの card 上限 (1 セクション内)
MAX_DETAIL_CARDS = 8

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


# ---------- 共通ブロックヘルパー ----------


def _header(prefix: str, emoji: str, text: str) -> dict[str, Any]:
    return {
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"{prefix} {emoji} {text}",
            "emoji": True,
        },
    }


def _divider() -> dict[str, Any]:
    return {"type": "divider"}


def _mention_banner(reason: str) -> dict[str, Any]:
    # header block は plain_text 専用なので <!channel> が解釈されない。
    # section + mrkdwn で出すと UI 上にもメンションが表示される。
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"<!channel> :bell: *{reason}*"},
    }


def _context_footer(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


# ---------- カードフォーマッタ (ソース別) ----------


def _jkk_card(prop: Property) -> list[dict[str, Any]]:
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


def _ur_card(prop: UrProperty) -> list[dict[str, Any]]:
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


def _suumo_card(prop: SuumoProperty) -> list[dict[str, Any]]:
    name_md = f"<{prop.detail_url}|{prop.name}>" if prop.detail_url else prop.name
    meta = " ・ ".join(
        part
        for part in (prop.area, prop.age, _format_commute(prop.commute))
        if part
    )
    text_lines = [f"*{name_md}*"]
    if meta:
        text_lines.append(meta)
    text_lines.extend(_format_suumo_access(prop.access))
    section: dict[str, Any] = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "\n".join(text_lines),
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


# ---------- サマリ行フォーマッタ (ソース別) ----------


def _jkk_summary_line(p: Property) -> str:
    return (
        f"• *{p.name}* ({p.area}) {p.layout} / {p.floor_area} m² / "
        f"{p.rent} 円 / {p.units} 戸"
    )


def _ur_summary_line(p: UrProperty) -> str:
    name_md = f"<{p.detail_url}|{p.name}>" if p.detail_url else p.name
    return (
        f"• *{name_md}* ({p.area}) {p.room_no} / {p.layout} / "
        f"{p.floor_area} / {p.rent}"
    )


# 生 access の 1 行 "路線/駅 歩N分" を分解。路線名に "/" は無く、駅は "/" の後ろ。
_SUUMO_ACCESS_RE = re.compile(r"^(.+?)/(.+?)\s*歩(\d+)分$")
_FLOOR_NUM_RE = re.compile(r"(\d+)")
_TOTAL_ABOVE_RE = re.compile(r"地上(\d+)階")  # "地下1地上35階建" → 35
_TOTAL_KEN_RE = re.compile(r"(\d+)階建")  # "11階建" → 11
_COMMUTE_RE = re.compile(r"^(.+?)駅（(.+?)）$")  # "渋谷駅（20分・0回）"


def _format_commute(raw: str) -> str:
    """物件の目的駅所要時間 "渋谷駅（20分・0回）" → "渋谷まで: 20分/0回"。

    形式が違えば原文のまま (捏造しない)。空なら空。
    """
    if not raw:
        return ""
    m = _COMMUTE_RE.match(raw)
    # 括弧内の区切りは中点 "20分・0回" だが、階数 "3/5階" と表記を揃えてスラッシュにする。
    return f"{m.group(1)}まで: {m.group(2).replace('・', '/')}" if m else raw


def _render_lines(lines: list[str]) -> str:
    """路線名リストを「連結ロゴ + 未登録路線名」表記にする。

    登録済み路線はロゴ絵文字 (重複除去)、未登録路線は日本語名のまま。
    例: ["京王線", "東急世田谷線"] → ":keio::tokyu-setagaya:"
        ["東急目黒線", "東急池上線"] → ":tokyu-meguro: 東急池上線"
    """
    emojis: list[str] = []
    texts: list[str] = []
    for line in lines:
        name = route_emoji(line)
        if name is None:
            if line not in texts:
                texts.append(line)
        elif name not in emojis:
            emojis.append(name)
    emoji_part = "".join(f":{n}:" for n in emojis)
    text_part = "/".join(texts)
    return " ".join(p for p in (emoji_part, text_part) if p)


def _format_suumo_access(access: str) -> list[str]:
    """生 access を (駅,徒歩分) 単位で集約し "ロゴ… 駅 歩N分" のリストにする。

    例: "京王線/下高井戸駅 歩4分 / 東急世田谷線/下高井戸駅 歩4分"
        → [":keio::tokyu-setagaya: 下高井戸駅 歩4分"]
    路線ロゴは駅単位で連結 (重複除去)。未登録路線は日本語名で表示。
    パースできないエントリは原文のまま末尾に残す (捏造しない)。
    """
    if not access:
        return []
    order: list[tuple[str, str]] = []  # (駅, 徒歩分) の出現順
    lines_by_key: dict[tuple[str, str], list[str]] = {}
    extras: list[str] = []
    for entry in access.split(" / "):
        entry = entry.strip()
        if not entry:
            continue
        m = _SUUMO_ACCESS_RE.match(entry)
        if not m:
            extras.append(entry)
            continue
        line, station, walk = m.group(1).strip(), m.group(2).strip(), m.group(3)
        key = (station, walk)
        if key not in lines_by_key:
            lines_by_key[key] = []
            order.append(key)
        if line not in lines_by_key[key]:
            lines_by_key[key].append(line)
    formatted = [
        f"{_render_lines(lines_by_key[(st, wk)])} {st} 歩{wk}分" for st, wk in order
    ]
    return formatted + extras


def _suumo_floors(floor: str, building_floors: str) -> str:
    """所在階と建物総階数を "6/35階" に。欠損時はあるものだけ表示。"""
    fm = _FLOOR_NUM_RE.search(floor or "")
    floor_n = fm.group(1) if fm else ""
    tm = _TOTAL_ABOVE_RE.search(building_floors or "") or _TOTAL_KEN_RE.search(
        building_floors or ""
    )
    total_n = tm.group(1) if tm else ""
    if floor_n and total_n:
        return f"{floor_n}/{total_n}階"
    if floor_n:
        return f"{floor_n}階"
    return building_floors or ""


# サマリ 3 行レイアウト (描画検証後に差し替え可能なよう定数化)。
# Slack section の mrkdwn は通常スペースを保持するが、詰まる場合は
# U+2007 (FIGURE SPACE) 等への差し替えを検討する。
_SUMMARY_INDENT = "     "  # line2/line3 の行頭インデント (5 spaces)
_STATION_SEP = "   "  # line3 の駅情報どうしの区切り (3 spaces)


def _suumo_summary_line(p: SuumoProperty) -> str:
    """物件 1 件を 3 行ブロックにする。

    例:
        •  *ファインスクェア明大前* (世田谷区)
             20万円 ・ 43.38m² ・ 3/5階 ・ 築6年 ・ 渋谷まで: 7分/0回
             :keio::tokyu-setagaya: 下高井戸駅 歩4分   :keio::keio-inokashira: 明大前駅 歩10分
    """
    name_md = f"<{p.detail_url}|{p.name}>" if p.detail_url else p.name
    lines = [f"•  *{name_md}* ({p.area})"]

    meta = [
        p.rent,
        p.floor_area,
        _suumo_floors(p.floor, p.building_floors),
        p.age,
        _format_commute(p.commute),
    ]
    body = " ・ ".join(part for part in meta if part)
    if body:
        lines.append(_SUMMARY_INDENT + body)

    access = _format_suumo_access(p.access)
    if access:
        lines.append(_SUMMARY_INDENT + _STATION_SEP.join(access))

    return "\n".join(lines)


# ---------- Renderer (ソース別の見た目を束ねる) ----------


@dataclass(frozen=True)
class SourceRenderer:
    source: Source
    label: str               # "[JKK]" 等の prefix
    bot_username: str
    bot_icon_emoji: str
    footer_text: str
    removed_label: str       # "件の物件が申し込まれました" 等
    card_formatter: Callable[[Any], list[dict[str, Any]]]
    summary_line_formatter: Callable[[Any], str]
    summary_separator: str = "\n"  # サマリの物件ブロック間の区切り ("\n\n" で空行)


JKK_RENDERER = SourceRenderer(
    source="jkk",
    label="[JKK]",
    bot_username=JKK_BOT_USERNAME,
    bot_icon_emoji=JKK_BOT_ICON_EMOJI,
    footer_text=(
        "Triggered by <https://github.com/tokyo3am/jkkwatcher|jkkwatcher>"
        f" · <{INIT_URL}|JKK で見る>"
    ),
    removed_label="件の物件が申し込まれました",
    card_formatter=_jkk_card,
    summary_line_formatter=_jkk_summary_line,
)

UR_RENDERER = SourceRenderer(
    source="ur",
    label="[UR]",
    bot_username=UR_BOT_USERNAME,
    bot_icon_emoji=UR_BOT_ICON_EMOJI,
    footer_text=(
        "Triggered by <https://github.com/tokyo3am/jkkwatcher|jkkwatcher>"
        f" · <{UR_SEARCH_URL}|UR で見る>"
    ),
    removed_label="件の物件が申し込まれました",
    card_formatter=_ur_card,
    summary_line_formatter=_ur_summary_line,
)

SUUMO_RENDERER = SourceRenderer(
    source="suumo",
    label="[Suumo]",
    bot_username=SUUMO_BOT_USERNAME,
    bot_icon_emoji=SUUMO_BOT_ICON_EMOJI,
    footer_text=(
        "Triggered by <https://github.com/tokyo3am/jkkwatcher|jkkwatcher>"
        f" · <{SUUMO_SEARCH_URL}|Suumo で見る>"
    ),
    removed_label="件の物件が掲載終了しました",
    card_formatter=_suumo_card,
    summary_line_formatter=_suumo_summary_line,
    summary_separator="\n\n",  # 物件と物件の間に空行を入れる
)


# ---------- メッセージ組み立てヘルパー ----------


def _make_message(
    renderer: SourceRenderer, *, text: str, blocks: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "username": renderer.bot_username,
        "icon_emoji": renderer.bot_icon_emoji,
        "text": text,
        "blocks": blocks,
    }


def _split_added(
    added: list[P], cfg: NotifyConfig, source: Source
) -> tuple[list[P], list[P]]:
    hits: list[P] = []
    others: list[P] = []
    for p in added:
        (hits if cfg.is_hit(source, p) else others).append(p)
    return hits, others


def _render_cards_section(
    label: str,
    emoji: str,
    label_text: str,
    items: list[P],
    card_formatter: Callable[[P], list[dict[str, Any]]],
    *,
    max_cards: int,
) -> list[dict[str, Any]]:
    """1 セクション分の cards を描画。max_cards 件まで表示。"""
    if not items:
        return []
    blocks: list[dict[str, Any]] = [
        _header(label, emoji, f"{len(items)} {label_text}"),
        _divider(),
    ]
    show = max(0, min(max_cards, len(items)))
    for prop in items[:show]:
        blocks.extend(card_formatter(prop))
        blocks.append(_divider())
    if len(items) > show:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"_他 {len(items) - show} 件は省略_",
                },
            }
        )
    return blocks


def _blocks_for_cards_section(n_show: int, n_total: int) -> int:
    """_render_cards_section が生成するブロック数を見積もる。"""
    if n_total == 0:
        return 0
    show = max(0, min(n_show, n_total))
    b = 2 + 2 * show  # header + divider + (card + divider) * show
    if n_total > show:
        b += 1  # truncated note
    return b


def _trim_diff_cards(
    n_others: int, n_removed: int, budget: int
) -> tuple[int, int]:
    """budget 内に収まるよう (others_show, removed_show) を決める。

    removed → others の順に削る (ユーザー要件: 「先に削除物件、次に新着物件を削る」)。
    """
    others_show = min(n_others, MAX_DETAIL_CARDS)
    removed_show = min(n_removed, MAX_DETAIL_CARDS)
    while (
        _blocks_for_cards_section(others_show, n_others)
        + _blocks_for_cards_section(removed_show, n_removed)
    ) > budget:
        if removed_show > 0:
            removed_show -= 1
        elif others_show > 0:
            others_show -= 1
        else:
            break
    return others_show, removed_show


# ---------- 3 メッセージビルダー ----------


def _build_hits_message(
    hits: list[P], renderer: SourceRenderer, *, mention: bool
) -> dict[str, Any] | None:
    if not hits:
        return None
    blocks: list[dict[str, Any]] = []
    if mention:
        blocks.append(
            _mention_banner(f"ウォッチリストに空きが出ました ({len(hits)} 件)")
        )
    blocks.extend(
        _render_cards_section(
            renderer.label,
            ":bell:",
            "件のウォッチ一致物件があります",
            hits,
            renderer.card_formatter,
            max_cards=MAX_DETAIL_CARDS,
        )
    )
    blocks.append(_context_footer(renderer.footer_text))

    prefix = "<!channel> " if mention else ""
    text = f"{prefix}{renderer.label} ウォッチ一致 {len(hits)} 件"
    return _make_message(renderer, text=text, blocks=blocks)


def _build_diff_message(
    others: list[P],
    removed: list[P],
    renderer: SourceRenderer,
    *,
    mention: bool,
    total_added: int,
) -> dict[str, Any] | None:
    if not others and not removed:
        return None

    blocks: list[dict[str, Any]] = []
    if mention:
        blocks.append(_mention_banner(f"新着 {total_added} 件"))

    overhead = (1 if mention else 0) + 1  # banner + context footer
    card_budget = BLOCKS_SAFE_LIMIT - overhead

    others_show, removed_show = _trim_diff_cards(len(others), len(removed), card_budget)

    blocks.extend(
        _render_cards_section(
            renderer.label,
            ":white_check_mark:",
            "件の新着物件があります",
            others,
            renderer.card_formatter,
            max_cards=others_show,
        )
    )
    blocks.extend(
        _render_cards_section(
            renderer.label,
            ":x:",
            renderer.removed_label,
            removed,
            renderer.card_formatter,
            max_cards=removed_show,
        )
    )
    blocks.append(_context_footer(renderer.footer_text))

    prefix = "<!channel> " if mention else ""
    text = f"{prefix}{renderer.label} 新着 {len(others)}件 / 終了 {len(removed)}件"
    return _make_message(renderer, text=text, blocks=blocks)


def _pack_lines_into_sections(
    lines: list[str], separator: str = "\n"
) -> list[dict[str, Any]]:
    """各行を 3000 char 上限の section block にまとめる (詰めるだけ詰める)。

    separator は行 (= 物件ブロック) どうしの区切り。"\n\n" で物件間に空行が入る。
    """
    sections: list[dict[str, Any]] = []
    cur_lines: list[str] = []
    cur_size = 0
    for line in lines:
        addition = len(line) + (len(separator) if cur_lines else 0)
        if cur_lines and cur_size + addition > SECTION_TEXT_SAFE_LIMIT:
            sections.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": separator.join(cur_lines)},
                }
            )
            cur_lines = [line]
            cur_size = len(line)
        else:
            cur_lines.append(line)
            cur_size += addition
    if cur_lines:
        sections.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": separator.join(cur_lines)},
            }
        )
    return sections


def _build_summary_messages(
    current: list[P], renderer: SourceRenderer
) -> list[dict[str, Any]]:
    """現在の空室状況を 1 通以上のメッセージに分割。常に最低 1 通返す。"""
    if not current:
        blocks = [
            _header(renderer.label, ":clipboard:", "現在の空室状況 0 件"),
            {"type": "section", "text": {"type": "mrkdwn", "text": "_該当物件なし_"}},
            _context_footer(renderer.footer_text),
        ]
        return [
            _make_message(
                renderer,
                text=f"{renderer.label} 現在の空室状況 0 件",
                blocks=blocks,
            )
        ]

    lines = [renderer.summary_line_formatter(p) for p in current]
    sections = _pack_lines_into_sections(lines, renderer.summary_separator)

    # 1 メッセージあたり: header (1) + sections (N) + context (1) <= BLOCKS_SAFE_LIMIT
    sections_per_message = max(1, BLOCKS_SAFE_LIMIT - 2)
    n_messages = (len(sections) + sections_per_message - 1) // sections_per_message

    messages: list[dict[str, Any]] = []
    for i in range(n_messages):
        chunk = sections[i * sections_per_message : (i + 1) * sections_per_message]
        suffix = f" ({i + 1}/{n_messages})" if n_messages > 1 else ""
        header_text = f"現在の空室状況 {len(current)} 件{suffix}"
        blocks = (
            [_header(renderer.label, ":clipboard:", header_text)]
            + chunk
            + [_context_footer(renderer.footer_text)]
        )
        messages.append(
            _make_message(
                renderer,
                text=f"{renderer.label} {header_text}",
                blocks=blocks,
            )
        )
    return messages


# ---------- 公開 API ----------


def build_messages(
    diff: Diff[P],
    current: list[P],
    renderer: SourceRenderer,
    *,
    notify_config: NotifyConfig | None = None,
) -> list[dict[str, Any]]:
    """3 種類のメッセージ (hits / diff / summary) を順に組んで返す。"""
    cfg = notify_config or NotifyConfig()
    hits, others = _split_added(diff.added, cfg, renderer.source)

    # メンションは hits メッセージ優先。hits 無し時のみ diff メッセージへ。
    mention_in_hits = bool(hits) and cfg.mention_on_watch_hit
    mention_in_diff = (
        not mention_in_hits and len(diff.added) > 0 and cfg.mention_on_added
    )

    messages: list[dict[str, Any]] = []
    msg_hits = _build_hits_message(hits, renderer, mention=mention_in_hits)
    if msg_hits is not None:
        messages.append(msg_hits)

    msg_diff = _build_diff_message(
        others, diff.removed, renderer,
        mention=mention_in_diff, total_added=len(diff.added),
    )
    if msg_diff is not None:
        messages.append(msg_diff)

    messages.extend(_build_summary_messages(current, renderer))
    return messages


def build_jkk_messages(
    diff: Diff[Property],
    current: list[Property],
    *,
    notify_config: NotifyConfig | None = None,
) -> list[dict[str, Any]]:
    return build_messages(diff, current, JKK_RENDERER, notify_config=notify_config)


def build_ur_messages(
    diff: Diff[UrProperty],
    current: list[UrProperty],
    *,
    notify_config: NotifyConfig | None = None,
) -> list[dict[str, Any]]:
    return build_messages(diff, current, UR_RENDERER, notify_config=notify_config)


def build_suumo_messages(
    diff: Diff[SuumoProperty],
    current: list[SuumoProperty],
    *,
    notify_config: NotifyConfig | None = None,
) -> list[dict[str, Any]]:
    return build_messages(diff, current, SUUMO_RENDERER, notify_config=notify_config)


def notify_all(
    webhook_url: str,
    payloads: list[dict[str, Any]],
    *,
    timeout: float = 10.0,
) -> None:
    """ペイロードを順に POST。途中でエラーが出たら raise (以降は送られない)。"""
    for i, payload in enumerate(payloads):
        resp = httpx.post(
            webhook_url,
            json=payload,
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200 or resp.text.strip() != "ok":
            raise RuntimeError(
                f"Slack webhook POST failed (message {i + 1}/{len(payloads)}): "
                f"status={resp.status_code} body={resp.text!r}"
            )
