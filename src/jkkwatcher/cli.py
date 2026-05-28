from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import diff as diff_mod
from . import notifier
from .models import KU_CODES, SKCS_CODES, Area, Property, SuumoProperty, UrProperty
from .scraper import JkkScraper
from .suumo_scraper import DEFAULT_SEARCH_URL as SUUMO_DEFAULT_URL, SuumoScraper
from .ur_scraper import UrScraper
from .watchlist import NotifyConfig


class OutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


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

    payload = notifier.build_payload(delta, current, notify_config=notify_config)
    if dry_run:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not webhook:
        console.print(
            "[red]差分はあるが Slack webhook 未指定 (--webhook / $SLACK_WEBHOOK_URL)。"
            "state も保存しません。"
        )
        raise typer.Exit(code=2)

    notifier.notify(webhook, payload)
    console.print("[green]Slack に通知しました。")
    diff_mod.save_state(state, current)


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

    payload = notifier.build_ur_payload(delta, current, notify_config=notify_config)
    if dry_run:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not webhook:
        console.print(
            "[red]差分はあるが Slack webhook 未指定 (--webhook / $SLACK_WEBHOOK_URL)。"
            "state も保存しません。"
        )
        raise typer.Exit(code=2)

    notifier.notify(webhook, payload)
    console.print("[green]Slack に通知しました。")
    diff_mod.save_state(state, current)


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
        str,
        typer.Option(
            "--url",
            help="Suumo 検索 URL。未指定時は組み込みのデフォルト URL を使用。",
        ),
    ] = SUUMO_DEFAULT_URL,
    output: Annotated[
        OutputFormat, typer.Option("--output", "-o", help="出力形式")
    ] = OutputFormat.TABLE,
) -> None:
    """Suumo の空き部屋を検索して一覧表示する。"""
    console = Console()
    with SuumoScraper(search_url=url) as scraper:
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
    url: Annotated[
        str,
        typer.Option(
            "--url",
            help="Suumo 検索 URL。未指定時は組み込みのデフォルト URL を使用。",
        ),
    ] = SUUMO_DEFAULT_URL,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Slack に投げず state 更新もスキップ。ペイロードを stdout に出すだけ",
        ),
    ] = False,
) -> None:
    """Suumo の空き部屋を取得し、前回 state との差分があれば Slack に通知する。"""
    console = Console(stderr=True)
    notify_config = NotifyConfig.from_env()
    with SuumoScraper(search_url=url) as scraper:
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

    payload = notifier.build_suumo_payload(delta, current, notify_config=notify_config)
    if dry_run:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not webhook:
        console.print(
            "[red]差分はあるが Slack webhook 未指定 (--webhook / $SLACK_WEBHOOK_URL)。"
            "state も保存しません。"
        )
        raise typer.Exit(code=2)

    notifier.notify(webhook, payload)
    console.print("[green]Slack に通知しました。")
    diff_mod.save_state(state, current)


@suumo_app.command("ids")
def suumo_ids(
    url: Annotated[
        str,
        typer.Option(
            "--url",
            help="Suumo 検索 URL。未指定時は組み込みのデフォルト URL を使用。",
        ),
    ] = SUUMO_DEFAULT_URL,
    output: Annotated[
        OutputFormat, typer.Option("--output", "-o", help="出力形式")
    ] = OutputFormat.TABLE,
) -> None:
    """Suumo の建物 key 一覧を出力する (watchlist 作成用)。"""
    console = Console()
    with SuumoScraper(search_url=url) as scraper:
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
        str,
        typer.Option(
            "--url",
            help="Suumo 検索 URL。未指定時は組み込みのデフォルト URL を使用。",
        ),
    ] = SUUMO_DEFAULT_URL,
    output: Annotated[
        OutputFormat, typer.Option("--output", "-o", help="出力形式")
    ] = OutputFormat.TABLE,
) -> None:
    """state-suumo.json と現在スクレイプ結果の差分だけを出す (state は更新しない)。"""
    console = Console(stderr=True)
    with SuumoScraper(search_url=url) as scraper:
        with console.status("[cyan]Suumo に問い合わせ中..."):
            current = scraper.search()

    previous = diff_mod.load_suumo_state(state)
    delta = diff_mod.compute(previous, current)
    _render_suumo_diff(delta, output, console)


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
