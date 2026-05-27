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
from .models import KU_CODES, SKCS_CODES, Area
from .scraper import JkkScraper
from .ur_scraper import UrScraper


class OutputFormat(str, Enum):
    TABLE = "table"
    JSON = "json"


app = typer.Typer(
    context_settings={"help_option_names": ["-h", "--help"]},
    help="JKK 東京 / UR 賃貸の空き物件監視ツール",
    no_args_is_help=True,
    add_completion=False,
)


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


@app.command()
def search(
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

    if output is OutputFormat.JSON:
        typer.echo(
            json.dumps(
                [p.to_dict() for p in properties],
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if not properties:
        console.print("[yellow]該当物件はありません。")
        return

    table = Table(title=f"先着順あき家 {len(properties)} 件")
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
            p.name,
            p.area,
            p.layout,
            p.floor_area,
            p.rent,
            p.common_fee,
            p.units,
            p.key,
        )
    console.print(table)


@app.command()
def watch(
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

    payload = notifier.build_payload(delta, current)

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


@app.command("ur-search")
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

    if output is OutputFormat.JSON:
        typer.echo(
            json.dumps(
                [p.to_dict() for p in properties],
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if not properties:
        console.print("[yellow]該当物件はありません。")
        return

    table = Table(title=f"UR 空き部屋 {len(properties)} 件")
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
    console.print(table)


@app.command("ur-watch")
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

    payload = notifier.build_ur_payload(delta, current)

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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
