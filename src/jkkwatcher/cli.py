from __future__ import annotations

import json
import os
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import diff as diff_mod
from . import notifier
from . import telegram
from .models import (
    KU_CODES,
    SKCS_CODES,
    Area,
    Property,
    PropertyLike,
    SuumoProperty,
    UrProperty,
)
from .scraper import JkkScraper
from .suumo_explain import SuumoSearchExplanation, explain_url
from .suumo_scraper import DEFAULT_SEARCH_URL as SUUMO_DEFAULT_URL, SuumoScraper
from .ur_scraper import UrScraper
from .watchlist import NotifyConfig, Source


class OutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


# Suumo 検索 URL の解決優先順位: --url > 環境変数 SUUMO_SEARCH_URL > 組み込みデフォルト。
# GitHub Actions の `vars.SUUMO_SEARCH_URL` 未設定時は空文字で渡るため、空も安全に
# フォールバックさせる (`or` 連鎖)。
SUUMO_URL_ENVVAR = "SUUMO_SEARCH_URL"


def _resolve_suumo_url(url: str | None) -> str:
    return url or os.environ.get(SUUMO_URL_ENVVAR) or SUUMO_DEFAULT_URL


def _explain_to_stderr(url: str, show: bool) -> None:
    """--explain 指定時、検索条件サマリを stderr に表示する。

    スクレイプ本体に余計な通信を足さないよう offline=True (無通信・辞書を書き換えない)。
    未知コードは「(未解決)」のまま表示する。
    """
    if not show:
        return
    explanation = explain_url(url, offline=True)
    _render_suumo_explain(explanation, OutputFormat.TABLE, Console(stderr=True))


# ---------- Telegram 通知 (Slack と独立した二次チャンネル) ----------
# 全差分をリッチに流す Slack と違い、watchlist ヒットのみをプレーンテキストで送る。


def _telegram_hit_texts(
    source: Source, added: Sequence[PropertyLike], notify_config: NotifyConfig
) -> list[str]:
    hits = [p for p in added if notify_config.is_hit(source, p)]
    return telegram.build_hit_messages(source, hits)


def _preview_telegram_hits(
    console: Console,
    source: Source,
    added: Sequence[PropertyLike],
    notify_config: NotifyConfig,
) -> None:
    """--dry-run 用。送るはずのテキストを stderr に出す (stdout の JSON を汚さない)。"""
    texts = _telegram_hit_texts(source, added, notify_config)
    if not texts:
        console.print("[dim]Telegram: ウォッチ一致なし (送信対象なし)")
        return
    console.print(f"[bold]Telegram プレビュー ({len(texts)} メッセージ):[/bold]")
    for text in texts:
        # 物件名に "[" が含まれても rich markup として解釈させない。
        console.print(text, markup=False, highlight=False)


def _notify_telegram_hits(
    console: Console,
    source: Source,
    added: Sequence[PropertyLike],
    notify_config: NotifyConfig,
    token: str | None,
    chat_id: str | None,
) -> None:
    """watchlist ヒットがあれば Telegram に通知する。

    両方未設定なら何もしない (Telegram は任意)。片方だけなら設定ミスとみなし、
    警告してスキップする。送信失敗は握り潰さず raise させる: ヒット時しか送らない
    ので、黙ってログに流すと見逃す。
    """
    if not token and not chat_id:
        return
    if not token or not chat_id:
        console.print(
            "[yellow]Telegram は TELEGRAM_BOT_TOKEN と TELEGRAM_CHAT_ID の"
            "両方が必要です。通知をスキップしました。"
        )
        return
    texts = _telegram_hit_texts(source, added, notify_config)
    if not texts:
        return
    telegram.notify(token, chat_id, texts)
    console.print(f"[green]Telegram に通知しました ({len(texts)} メッセージ)。")


app = typer.Typer(
    context_settings={"help_option_names": ["-h", "--help"]},
    help="JKK 東京 / UR 賃貸の空き物件監視ツール",
    no_args_is_help=True,
    add_completion=False,
)

jkk_app = typer.Typer(
    help="JKK (東京都住宅供給公社) の空き物件",
    no_args_is_help=True,
)
ur_app = typer.Typer(
    help="UR (都市再生機構) の空き物件",
    no_args_is_help=True,
)
suumo_app = typer.Typer(
    help="Suumo (民間賃貸) の空き部屋",
    no_args_is_help=True,
)
app.add_typer(jkk_app, name="jkk")
app.add_typer(ur_app, name="ur")
app.add_typer(suumo_app, name="suumo")


def _validate_ku_codes(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    invalid = [v for v in values if v not in KU_CODES]
    if invalid:
        raise typer.BadParameter(
            f"無効な区コード: {invalid}. 例: 01(千代田区)〜23(江戸川区)"
        )
    return values


def _validate_skcs_codes(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    invalid = [v for v in values if v not in SKCS_CODES]
    if invalid:
        raise typer.BadParameter(
            f"無効な skcs コード: {invalid}. 有効値: {list(SKCS_CODES.keys())}"
        )
    return values


# ---------- JKK ----------


@jkk_app.command("search")
def jkk_search(
    area: Annotated[
        Area, typer.Option("--area", "-a", help="検索エリア (ku=区部 / shi=市部)")
    ] = Area.KU,
    ku: Annotated[
        list[str] | None,
        typer.Option(
            "--ku",
            "-k",
            help="区コード (例: 12 で世田谷区のみ)。複数可。未指定時は全区。",
            callback=_validate_ku_codes,
        ),
    ] = None,
    output: Annotated[
        OutputFormat, typer.Option("--output", "-o", help="出力形式")
    ] = OutputFormat.TABLE,
) -> None:
    """JKK 区部/市部の空き物件を検索して一覧表示する。"""
    console = Console()
    with JkkScraper() as scraper:
        with console.status("[cyan]JKKねっとに問い合わせ中..."):
            properties = scraper.search(ku_codes=ku, area=area)
    _render_jkk(properties, output, console)


@jkk_app.command("watch")
def jkk_watch(
    state: Annotated[
        Path,
        typer.Option("--state", "-s", help="前回結果を保存する JSON ファイル"),
    ] = Path("state.json"),
    webhook: Annotated[
        str | None,
        typer.Option(
            "--webhook",
            "-w",
            envvar="SLACK_WEBHOOK_URL",
            help="Slack Incoming Webhook URL (env: SLACK_WEBHOOK_URL)",
        ),
    ] = None,
    telegram_token: Annotated[
        str | None,
        typer.Option(
            "--telegram-token",
            envvar="TELEGRAM_BOT_TOKEN",
            help="Telegram Bot API token (env: TELEGRAM_BOT_TOKEN)",
        ),
    ] = None,
    telegram_chat_id: Annotated[
        str | None,
        typer.Option(
            "--telegram-chat-id",
            envvar="TELEGRAM_CHAT_ID",
            help="Telegram の送信先 chat ID (env: TELEGRAM_CHAT_ID)",
        ),
    ] = None,
    area: Annotated[
        Area, typer.Option("--area", "-a", help="検索エリア (ku=区部 / shi=市部)")
    ] = Area.KU,
    ku: Annotated[
        list[str] | None,
        typer.Option(
            "--ku",
            "-k",
            help="区コード (例: 12 で世田谷区のみ)。複数可。未指定時は全区。",
            callback=_validate_ku_codes,
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Slack に投げず state 更新もスキップ。ペイロードを stdout に出すだけ",
        ),
    ] = False,
) -> None:
    """JKK の空き物件を取得し、前回 state との差分があれば Slack に通知する。"""
    console = Console(stderr=True)
    notify_config = NotifyConfig.from_env()
    with JkkScraper() as scraper:
        with console.status("[cyan]JKKねっとに問い合わせ中..."):
            current = scraper.search(ku_codes=ku, area=area)

    previous = diff_mod.load_jkk_state(state)
    delta = diff_mod.compute(previous, current)
    console.print(
        f"[bold]差分:[/bold] 新着 {len(delta.added)} 件 / "
        f"終了 {len(delta.removed)} 件 / 現在 {len(current)} 件"
    )

    if delta.is_empty:
        console.print("[green]差分なし。通知をスキップしました。")
        if not dry_run:
            diff_mod.save_state(state, current)
        return

    payloads = notifier.build_jkk_messages(delta, current, notify_config=notify_config)
    if dry_run:
        typer.echo(json.dumps(payloads, ensure_ascii=False, indent=2))
        _preview_telegram_hits(console, "jkk", delta.added, notify_config)
        return

    if not webhook:
        console.print(
            "[red]差分はあるが Slack webhook 未指定 (--webhook / $SLACK_WEBHOOK_URL)。"
            "state も保存しません。"
        )
        raise typer.Exit(code=2)

    notifier.notify_all(webhook, payloads)
    console.print(f"[green]Slack に通知しました ({len(payloads)} メッセージ)。")
    diff_mod.save_state(state, current)
    # state 保存後に送る: Telegram が落ちても次回実行で Slack 通知が重複しない。
    _notify_telegram_hits(
        console, "jkk", delta.added, notify_config, telegram_token, telegram_chat_id
    )


@jkk_app.command("ids")
def jkk_ids(
    area: Annotated[
        Area, typer.Option("--area", "-a", help="検索エリア (ku=区部 / shi=市部)")
    ] = Area.KU,
    ku: Annotated[
        list[str] | None,
        typer.Option(
            "--ku",
            "-k",
            help="区コード (例: 12 で世田谷区のみ)。複数可。未指定時は全区。",
            callback=_validate_ku_codes,
        ),
    ] = None,
    output: Annotated[
        OutputFormat, typer.Option("--output", "-o", help="出力形式")
    ] = OutputFormat.TABLE,
) -> None:
    """JKK の建物 key 一覧を出力する (watchlist 作成用)。"""
    console = Console()
    with JkkScraper() as scraper:
        with console.status("[cyan]JKKねっとに問い合わせ中..."):
            properties = scraper.search(ku_codes=ku, area=area)
    _render_building_ids(properties, output, console)


@jkk_app.command("diff")
def jkk_diff(
    state: Annotated[
        Path,
        typer.Option("--state", "-s", help="比較対象の state JSON ファイル"),
    ] = Path("state.json"),
    area: Annotated[
        Area, typer.Option("--area", "-a", help="検索エリア (ku=区部 / shi=市部)")
    ] = Area.KU,
    ku: Annotated[
        list[str] | None,
        typer.Option(
            "--ku",
            "-k",
            help="区コード。複数可。未指定時は全区。",
            callback=_validate_ku_codes,
        ),
    ] = None,
    output: Annotated[
        OutputFormat, typer.Option("--output", "-o", help="出力形式")
    ] = OutputFormat.TABLE,
) -> None:
    """state.json と現在スクレイプ結果の差分だけを出す (state は更新しない)。"""
    console = Console(stderr=True)
    with JkkScraper() as scraper:
        with console.status("[cyan]JKKねっとに問い合わせ中..."):
            current = scraper.search(ku_codes=ku, area=area)

    previous = diff_mod.load_jkk_state(state)
    delta = diff_mod.compute(previous, current)
    _render_jkk_diff(delta, output, console)


# ---------- UR ----------


@ur_app.command("search")
def ur_search(
    skcs: Annotated[
        list[str] | None,
        typer.Option(
            "--skcs",
            help=(
                "UR の skcs (sub-area) コード。複数可。未指定時は東京23区相当の全コード。"
                f" 有効値: {list(SKCS_CODES.keys())}"
            ),
            callback=_validate_skcs_codes,
        ),
    ] = None,
    output: Annotated[
        OutputFormat, typer.Option("--output", "-o", help="出力形式")
    ] = OutputFormat.TABLE,
) -> None:
    """UR (東京23区) の空き部屋を検索して一覧表示する。"""
    console = Console()
    with UrScraper() as scraper:
        with console.status("[cyan]UR-net に問い合わせ中..."):
            properties = scraper.search(skcs_codes=skcs)
    _render_ur(properties, output, console)


@ur_app.command("watch")
def ur_watch(
    state: Annotated[
        Path,
        typer.Option("--state", "-s", help="前回結果を保存する JSON ファイル"),
    ] = Path("state-ur.json"),
    webhook: Annotated[
        str | None,
        typer.Option(
            "--webhook",
            "-w",
            envvar="SLACK_WEBHOOK_URL",
            help="Slack Incoming Webhook URL (env: SLACK_WEBHOOK_URL)",
        ),
    ] = None,
    telegram_token: Annotated[
        str | None,
        typer.Option(
            "--telegram-token",
            envvar="TELEGRAM_BOT_TOKEN",
            help="Telegram Bot API token (env: TELEGRAM_BOT_TOKEN)",
        ),
    ] = None,
    telegram_chat_id: Annotated[
        str | None,
        typer.Option(
            "--telegram-chat-id",
            envvar="TELEGRAM_CHAT_ID",
            help="Telegram の送信先 chat ID (env: TELEGRAM_CHAT_ID)",
        ),
    ] = None,
    skcs: Annotated[
        list[str] | None,
        typer.Option(
            "--skcs",
            help=(
                "UR の skcs (sub-area) コード。複数可。未指定時は東京23区相当の全コード。"
                f" 有効値: {list(SKCS_CODES.keys())}"
            ),
            callback=_validate_skcs_codes,
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Slack に投げず state 更新もスキップ。ペイロードを stdout に出すだけ",
        ),
    ] = False,
) -> None:
    """UR の空き部屋を取得し、前回 state との差分があれば Slack に通知する。"""
    console = Console(stderr=True)
    notify_config = NotifyConfig.from_env()
    with UrScraper() as scraper:
        with console.status("[cyan]UR-net に問い合わせ中..."):
            current = scraper.search(skcs_codes=skcs)

    previous = diff_mod.load_ur_state(state)
    delta = diff_mod.compute(previous, current)
    console.print(
        f"[bold]差分:[/bold] 新着 {len(delta.added)} 件 / "
        f"終了 {len(delta.removed)} 件 / 現在 {len(current)} 件"
    )

    if delta.is_empty:
        console.print("[green]差分なし。通知をスキップしました。")
        if not dry_run:
            diff_mod.save_state(state, current)
        return

    payloads = notifier.build_ur_messages(delta, current, notify_config=notify_config)
    if dry_run:
        typer.echo(json.dumps(payloads, ensure_ascii=False, indent=2))
        _preview_telegram_hits(console, "ur", delta.added, notify_config)
        return

    if not webhook:
        console.print(
            "[red]差分はあるが Slack webhook 未指定 (--webhook / $SLACK_WEBHOOK_URL)。"
            "state も保存しません。"
        )
        raise typer.Exit(code=2)

    notifier.notify_all(webhook, payloads)
    console.print(f"[green]Slack に通知しました ({len(payloads)} メッセージ)。")
    diff_mod.save_state(state, current)
    # state 保存後に送る: Telegram が落ちても次回実行で Slack 通知が重複しない。
    _notify_telegram_hits(
        console, "ur", delta.added, notify_config, telegram_token, telegram_chat_id
    )


@ur_app.command("ids")
def ur_ids(
    skcs: Annotated[
        list[str] | None,
        typer.Option(
            "--skcs",
            help=(
                "UR の skcs (sub-area) コード。複数可。未指定時は東京23区相当の全コード。"
                f" 有効値: {list(SKCS_CODES.keys())}"
            ),
            callback=_validate_skcs_codes,
        ),
    ] = None,
    output: Annotated[
        OutputFormat, typer.Option("--output", "-o", help="出力形式")
    ] = OutputFormat.TABLE,
) -> None:
    """UR の建物 key 一覧を出力する (watchlist 作成用)。"""
    console = Console()
    with UrScraper() as scraper:
        with console.status("[cyan]UR-net に問い合わせ中..."):
            properties = scraper.search(skcs_codes=skcs)
    _render_building_ids(properties, output, console)


@ur_app.command("diff")
def ur_diff(
    state: Annotated[
        Path,
        typer.Option("--state", "-s", help="比較対象の state JSON ファイル"),
    ] = Path("state-ur.json"),
    skcs: Annotated[
        list[str] | None,
        typer.Option(
            "--skcs",
            help="UR の skcs。複数可。未指定時は東京23区相当の全コード。",
            callback=_validate_skcs_codes,
        ),
    ] = None,
    output: Annotated[
        OutputFormat, typer.Option("--output", "-o", help="出力形式")
    ] = OutputFormat.TABLE,
) -> None:
    """state-ur.json と現在スクレイプ結果の差分だけを出す (state は更新しない)。"""
    console = Console(stderr=True)
    with UrScraper() as scraper:
        with console.status("[cyan]UR-net に問い合わせ中..."):
            current = scraper.search(skcs_codes=skcs)

    previous = diff_mod.load_ur_state(state)
    delta = diff_mod.compute(previous, current)
    _render_ur_diff(delta, output, console)


# ---------- Suumo ----------


@suumo_app.command("search")
def suumo_search(
    url: Annotated[
        str | None,
        typer.Option(
            "--url",
            help=(
                "Suumo 検索 URL。未指定時は環境変数 $SUUMO_SEARCH_URL、"
                "無ければ組み込みのデフォルト URL を使用。"
            ),
        ),
    ] = None,
    output: Annotated[
        OutputFormat, typer.Option("--output", "-o", help="出力形式")
    ] = OutputFormat.TABLE,
    explain: Annotated[
        bool,
        typer.Option(
            "--explain", help="検索条件を人間可読に解釈して stderr に表示する。"
        ),
    ] = False,
) -> None:
    """Suumo の空き部屋を検索して一覧表示する。"""
    console = Console()
    resolved_url = _resolve_suumo_url(url)
    _explain_to_stderr(resolved_url, explain)
    with SuumoScraper(search_url=resolved_url) as scraper:
        with console.status("[cyan]Suumo に問い合わせ中..."):
            properties = scraper.search()
    _render_suumo(properties, output, console)


@suumo_app.command("watch")
def suumo_watch(
    state: Annotated[
        Path,
        typer.Option("--state", "-s", help="前回結果を保存する JSON ファイル"),
    ] = Path("state-suumo.json"),
    webhook: Annotated[
        str | None,
        typer.Option(
            "--webhook",
            "-w",
            envvar="SLACK_WEBHOOK_URL",
            help="Slack Incoming Webhook URL (env: SLACK_WEBHOOK_URL)",
        ),
    ] = None,
    telegram_token: Annotated[
        str | None,
        typer.Option(
            "--telegram-token",
            envvar="TELEGRAM_BOT_TOKEN",
            help="Telegram Bot API token (env: TELEGRAM_BOT_TOKEN)",
        ),
    ] = None,
    telegram_chat_id: Annotated[
        str | None,
        typer.Option(
            "--telegram-chat-id",
            envvar="TELEGRAM_CHAT_ID",
            help="Telegram の送信先 chat ID (env: TELEGRAM_CHAT_ID)",
        ),
    ] = None,
    url: Annotated[
        str | None,
        typer.Option(
            "--url",
            help=(
                "Suumo 検索 URL。未指定時は環境変数 $SUUMO_SEARCH_URL、"
                "無ければ組み込みのデフォルト URL を使用。"
            ),
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Slack に投げず state 更新もスキップ。ペイロードを stdout に出すだけ",
        ),
    ] = False,
    explain: Annotated[
        bool,
        typer.Option(
            "--explain", help="検索条件を人間可読に解釈して stderr に表示する。"
        ),
    ] = False,
) -> None:
    """Suumo の空き部屋を取得し、前回 state との差分があれば Slack に通知する。"""
    console = Console(stderr=True)
    notify_config = NotifyConfig.from_env()
    resolved_url = _resolve_suumo_url(url)
    _explain_to_stderr(resolved_url, explain)
    with SuumoScraper(search_url=resolved_url) as scraper:
        with console.status("[cyan]Suumo に問い合わせ中..."):
            current = scraper.search()

    previous = diff_mod.load_suumo_state(state)
    delta = diff_mod.compute(previous, current)
    console.print(
        f"[bold]差分:[/bold] 新着 {len(delta.added)} 件 / "
        f"終了 {len(delta.removed)} 件 / 現在 {len(current)} 件"
    )

    if delta.is_empty:
        console.print("[green]差分なし。通知をスキップしました。")
        if not dry_run:
            diff_mod.save_state(state, current)
        return

    payloads = notifier.build_suumo_messages(delta, current, notify_config=notify_config)
    if dry_run:
        typer.echo(json.dumps(payloads, ensure_ascii=False, indent=2))
        _preview_telegram_hits(console, "suumo", delta.added, notify_config)
        return

    if not webhook:
        console.print(
            "[red]差分はあるが Slack webhook 未指定 (--webhook / $SLACK_WEBHOOK_URL)。"
            "state も保存しません。"
        )
        raise typer.Exit(code=2)

    notifier.notify_all(webhook, payloads)
    console.print(f"[green]Slack に通知しました ({len(payloads)} メッセージ)。")
    diff_mod.save_state(state, current)
    # state 保存後に送る: Telegram が落ちても次回実行で Slack 通知が重複しない。
    _notify_telegram_hits(
        console, "suumo", delta.added, notify_config, telegram_token, telegram_chat_id
    )


@suumo_app.command("ids")
def suumo_ids(
    url: Annotated[
        str | None,
        typer.Option(
            "--url",
            help=(
                "Suumo 検索 URL。未指定時は環境変数 $SUUMO_SEARCH_URL、"
                "無ければ組み込みのデフォルト URL を使用。"
            ),
        ),
    ] = None,
    output: Annotated[
        OutputFormat, typer.Option("--output", "-o", help="出力形式")
    ] = OutputFormat.TABLE,
    explain: Annotated[
        bool,
        typer.Option(
            "--explain", help="検索条件を人間可読に解釈して stderr に表示する。"
        ),
    ] = False,
) -> None:
    """Suumo の建物 key 一覧を出力する (watchlist 作成用)。"""
    console = Console()
    resolved_url = _resolve_suumo_url(url)
    _explain_to_stderr(resolved_url, explain)
    with SuumoScraper(search_url=resolved_url) as scraper:
        with console.status("[cyan]Suumo に問い合わせ中..."):
            properties = scraper.search()
    _render_building_ids(properties, output, console)


@suumo_app.command("diff")
def suumo_diff(
    state: Annotated[
        Path,
        typer.Option("--state", "-s", help="比較対象の state JSON ファイル"),
    ] = Path("state-suumo.json"),
    url: Annotated[
        str | None,
        typer.Option(
            "--url",
            help=(
                "Suumo 検索 URL。未指定時は環境変数 $SUUMO_SEARCH_URL、"
                "無ければ組み込みのデフォルト URL を使用。"
            ),
        ),
    ] = None,
    output: Annotated[
        OutputFormat, typer.Option("--output", "-o", help="出力形式")
    ] = OutputFormat.TABLE,
    explain: Annotated[
        bool,
        typer.Option(
            "--explain", help="検索条件を人間可読に解釈して stderr に表示する。"
        ),
    ] = False,
) -> None:
    """state-suumo.json と現在スクレイプ結果の差分だけを出す (state は更新しない)。"""
    console = Console(stderr=True)
    resolved_url = _resolve_suumo_url(url)
    _explain_to_stderr(resolved_url, explain)
    with SuumoScraper(search_url=resolved_url) as scraper:
        with console.status("[cyan]Suumo に問い合わせ中..."):
            current = scraper.search()

    previous = diff_mod.load_suumo_state(state)
    delta = diff_mod.compute(previous, current)
    _render_suumo_diff(delta, output, console)


@suumo_app.command("explain")
def suumo_explain(
    url: Annotated[
        str | None,
        typer.Option(
            "--url",
            help=(
                "解釈する Suumo 検索 URL。未指定時は環境変数 $SUUMO_SEARCH_URL、"
                "無ければ組み込みのデフォルト URL を使用。"
            ),
        ),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="オンライン解決を行わず辞書のみで解釈する。"),
    ] = False,
    output: Annotated[
        OutputFormat, typer.Option("--output", "-o", help="出力形式")
    ] = OutputFormat.TABLE,
) -> None:
    """Suumo 検索 URL を人間可読に解釈する。未知コードはオンライン解決して辞書へ追記。"""
    resolved_url = _resolve_suumo_url(url)
    console = Console()
    if offline:
        explanation = explain_url(resolved_url, offline=True)
    else:
        with console.status("[cyan]未知コードをオンライン解決中..."):
            explanation = explain_url(resolved_url, offline=False)
    _render_suumo_explain(explanation, output, console)


# ---------- renderers ----------


def _render_building_ids(
    properties: list[Property] | list[UrProperty] | list[SuumoProperty],
    output: OutputFormat,
    console: Console,
) -> None:
    # building_key 単位で 1 件にまとめる (同じ建物の複数部屋は集約)。
    seen: dict[str, tuple[str, str]] = {}
    for p in properties:
        if p.building_key not in seen:
            seen[p.building_key] = (p.name, p.area)

    if output is OutputFormat.JSON:
        typer.echo(
            json.dumps(
                [
                    {"building_key": k, "name": n, "area": a}
                    for k, (n, a) in seen.items()
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if not seen:
        console.print("[yellow]該当物件はありません。")
        return
    table = Table(title=f"建物 ID 一覧 ({len(seen)} 件)")
    table.add_column("建物 Key", style="cyan", no_wrap=True)
    table.add_column("物件名")
    table.add_column("地域", style="magenta")
    for k, (n, a) in seen.items():
        table.add_row(k, n, a)
    console.print(table)


def _render_jkk(
    properties: list[Property], output: OutputFormat, console: Console
) -> None:
    if output is OutputFormat.JSON:
        typer.echo(
            json.dumps([p.to_dict() for p in properties], ensure_ascii=False, indent=2)
        )
        return
    if not properties:
        console.print("[yellow]該当物件はありません。")
        return
    console.print(_jkk_table(properties, title=f"先着順あき家 {len(properties)} 件"))


def _render_ur(
    properties: list[UrProperty], output: OutputFormat, console: Console
) -> None:
    if output is OutputFormat.JSON:
        typer.echo(
            json.dumps([p.to_dict() for p in properties], ensure_ascii=False, indent=2)
        )
        return
    if not properties:
        console.print("[yellow]該当物件はありません。")
        return
    console.print(_ur_table(properties, title=f"UR 空き部屋 {len(properties)} 件"))


def _render_jkk_diff(
    delta: diff_mod.Diff[Property], output: OutputFormat, console: Console
) -> None:
    if output is OutputFormat.JSON:
        typer.echo(
            json.dumps(
                {
                    "added": [p.to_dict() for p in delta.added],
                    "removed": [p.to_dict() for p in delta.removed],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if delta.is_empty:
        console.print("[green]差分なし。")
        return
    if delta.added:
        console.print(
            _jkk_table(
                delta.added,
                title=f"[green]+ 新着 {len(delta.added)} 件",
                style="green",
            )
        )
    if delta.removed:
        console.print(
            _jkk_table(
                delta.removed,
                title=f"[red]- 終了 {len(delta.removed)} 件",
                style="red",
            )
        )


def _render_ur_diff(
    delta: diff_mod.Diff[UrProperty], output: OutputFormat, console: Console
) -> None:
    if output is OutputFormat.JSON:
        typer.echo(
            json.dumps(
                {
                    "added": [p.to_dict() for p in delta.added],
                    "removed": [p.to_dict() for p in delta.removed],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if delta.is_empty:
        console.print("[green]差分なし。")
        return
    if delta.added:
        console.print(
            _ur_table(
                delta.added,
                title=f"[green]+ 新着 {len(delta.added)} 件",
                style="green",
            )
        )
    if delta.removed:
        console.print(
            _ur_table(
                delta.removed,
                title=f"[red]- 終了 {len(delta.removed)} 件",
                style="red",
            )
        )


def _jkk_table(
    properties: list[Property], *, title: str, style: str | None = None
) -> Table:
    table = Table(title=title, title_style=style)
    table.add_column("住宅名", style="cyan", no_wrap=False)
    table.add_column("地域", style="magenta")
    table.add_column("間取り")
    table.add_column("床面積[m²]", justify="right")
    table.add_column("家賃[円]", justify="right", style="green")
    table.add_column("共益費[円]", justify="right")
    table.add_column("戸数", justify="right")
    table.add_column("ID")
    for p in properties:
        table.add_row(
            p.name, p.area, p.layout, p.floor_area, p.rent, p.common_fee, p.units, p.key
        )
    return table


def _ur_table(
    properties: list[UrProperty], *, title: str, style: str | None = None
) -> Table:
    table = Table(title=title, title_style=style)
    table.add_column("物件名", style="cyan", no_wrap=False)
    table.add_column("地域", style="magenta")
    table.add_column("号室")
    table.add_column("間取り")
    table.add_column("床面積")
    table.add_column("階", justify="right")
    table.add_column("家賃", justify="right", style="green")
    table.add_column("共益費", justify="right")
    table.add_column("ID")
    for p in properties:
        table.add_row(
            p.name,
            p.area,
            p.room_no,
            p.layout,
            p.floor_area,
            p.floor,
            p.rent,
            p.common_fee,
            p.room_id,
        )
    return table


def _render_suumo(
    properties: list[SuumoProperty], output: OutputFormat, console: Console
) -> None:
    if output is OutputFormat.JSON:
        typer.echo(
            json.dumps([p.to_dict() for p in properties], ensure_ascii=False, indent=2)
        )
        return
    if not properties:
        console.print("[yellow]該当物件はありません。")
        return
    console.print(_suumo_table(properties, title=f"Suumo 空き部屋 {len(properties)} 件"))


def _render_suumo_diff(
    delta: diff_mod.Diff[SuumoProperty], output: OutputFormat, console: Console
) -> None:
    if output is OutputFormat.JSON:
        typer.echo(
            json.dumps(
                {
                    "added": [p.to_dict() for p in delta.added],
                    "removed": [p.to_dict() for p in delta.removed],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if delta.is_empty:
        console.print("[green]差分なし。")
        return
    if delta.added:
        console.print(
            _suumo_table(
                delta.added,
                title=f"[green]+ 新着 {len(delta.added)} 件",
                style="green",
            )
        )
    if delta.removed:
        console.print(
            _suumo_table(
                delta.removed,
                title=f"[red]- 終了 {len(delta.removed)} 件",
                style="red",
            )
        )


def _suumo_table(
    properties: list[SuumoProperty], *, title: str, style: str | None = None
) -> Table:
    table = Table(title=title, title_style=style)
    table.add_column("物件名", style="cyan", no_wrap=False)
    table.add_column("地域", style="magenta")
    table.add_column("築年")
    table.add_column("階", justify="right")
    table.add_column("間取り")
    table.add_column("床面積")
    table.add_column("家賃", justify="right", style="green")
    table.add_column("管理費", justify="right")
    table.add_column("ID")
    for p in properties:
        table.add_row(
            p.name,
            p.area,
            p.age,
            p.floor,
            p.layout,
            p.floor_area,
            p.rent,
            p.common_fee,
            p.jnc,
        )
    return table


def _render_suumo_explain(
    exp: SuumoSearchExplanation, output: OutputFormat, console: Console
) -> None:
    if output is OutputFormat.JSON:
        typer.echo(json.dumps(exp.to_dict(), ensure_ascii=False, indent=2))
        return

    table = Table(title="Suumo 検索条件")
    table.add_column("項目", style="cyan", no_wrap=True)
    table.add_column("内容")

    def row(label: str, value: str | None) -> None:
        if value:
            table.add_row(label, value)

    row("都道府県", exp.prefecture.display if exp.prefecture else None)
    row("地方", exp.region.display if exp.region else None)
    row("物件種別", exp.property_kind.display if exp.property_kind else None)
    row("建物種別", " / ".join(c.display for c in exp.building_types))
    row("賃料", exp.rent)
    row("専有面積", exp.floor_area)
    row("築年数", exp.building_age)
    row("駅", exp.station.display if exp.station else None)
    row("駅徒歩", exp.walk_minutes)
    row("通勤条件", exp.commute)
    if exp.lines:
        row(f"沿線 ({len(exp.lines)}本)", "\n".join(c.display for c in exp.lines))
    if exp.features:
        row("こだわり条件", "\n".join(c.display for c in exp.features))
    row("並び順", exp.sort_order.display if exp.sort_order else None)
    console.print(table)

    extras = exp.uncertain + exp.unresolved
    if extras:
        warn = Table(title="その他・未解決 (推定/未確定)", title_style="yellow")
        warn.add_column("内容", style="dim")
        for line in extras:
            warn.add_row(line)
        console.print(warn)

    if exp.newly_resolved:
        console.print(
            f"[green]辞書に {len(exp.newly_resolved)} 件を追記しました "
            "(オンライン解決)。[/green]"
        )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
