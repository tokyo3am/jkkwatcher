"""ekidata.jp の line.csv / station.csv から駅→路線辞書 (station_lines.json) を生成。

ekidata CSV の入手手順は docs/ekidata.md を参照。ネット不要・標準ライブラリのみ。

使用例:
    uv run python scripts/gen_station_lines.py \
        --station data/raw/station.csv \
        --line    data/raw/line.csv \
        --report-unmapped \
        --generated-at $(date +%F)

--report-unmapped を付けると、生成辞書に出る全路線のうち Slack 絵文字が未登録
(route_emoji=None) のものを出現駅数つきで出力する (line_emoji.py に追記すべき
主要路線の優先度リスト)。
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

import json

# 関東 1 都 6 県 (茨城・栃木・群馬・埼玉・千葉・東京・神奈川)。
_KANTO_PREF_CDS = (8, 9, 10, 11, 12, 13, 14)
_E_STATUS_ACTIVE = "0"  # 0:運用中 1:運用前 2:廃止


def _require_columns(
    reader: csv.DictReader, required: set[str], path: Path
) -> None:
    """ヘッダに必要カラムが無ければ分かりやすく落とす (Fail Loudly)。"""
    have = set(reader.fieldnames or [])
    missing = required - have
    if missing:
        raise SystemExit(
            f"[error] {path} に必要なカラムがありません: {sorted(missing)}\n"
            f"        ekidata のヘッダ付き CSV か確認してください "
            f"(見つかったヘッダ: {sorted(have)})"
        )


def _to_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _load_line_names(line_csv: Path) -> dict[str, str]:
    """line.csv → {line_cd: line_name} (運用中のみ)。"""
    names: dict[str, str] = {}
    with line_csv.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        _require_columns(reader, {"line_cd", "line_name", "e_status"}, line_csv)
        for row in reader:
            if (row.get("e_status") or "0").strip() != _E_STATUS_ACTIVE:
                continue
            names[row["line_cd"].strip()] = row["line_name"].strip()
    return names


def _build_stations(
    station_csv: Path,
    line_names: dict[str, str],
    pref_cds: set[int],
) -> dict[str, list[dict]]:
    """station.csv → {駅名: [候補...]}。station_g_cd でグループ化。"""
    groups: dict[str, dict] = {}
    lines_in_group: dict[str, list[str]] = defaultdict(list)  # g_cd -> line_name 出現順
    with station_csv.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        _require_columns(
            reader,
            {"station_g_cd", "station_name", "line_cd", "pref_cd", "e_status"},
            station_csv,
        )
        for row in reader:
            if (row.get("e_status") or "0").strip() != _E_STATUS_ACTIVE:
                continue
            try:
                pref = int(row["pref_cd"])
            except (KeyError, ValueError):
                continue
            if pref not in pref_cds:
                continue
            # 運用中の路線マスタに無い line_cd (廃線/運用前) はスキップ。
            line_name = line_names.get(row["line_cd"].strip())
            if line_name is None:
                continue
            g_cd = row["station_g_cd"].strip()
            if g_cd not in groups:
                groups[g_cd] = {
                    "station_name": row["station_name"].strip(),
                    "pref_cd": pref,
                    "station_g_cd": int(g_cd),
                    "lat": _to_float(row.get("lat")),
                    "lon": _to_float(row.get("lon")),
                }
            if line_name not in lines_in_group[g_cd]:
                lines_in_group[g_cd].append(line_name)

    stations: dict[str, list[dict]] = defaultdict(list)
    for g_cd, g in groups.items():
        stations[g["station_name"]].append(
            {
                "lines": lines_in_group[g_cd],
                "pref_cd": g["pref_cd"],
                "station_g_cd": g["station_g_cd"],
                "lat": g["lat"],
                "lon": g["lon"],
            }
        )
    return dict(stations)


def _report_unmapped(stations: dict[str, list[dict]]) -> None:
    """辞書に出る全路線のうち route_emoji が未登録のものを出現駅数順に出力。"""
    from jkkwatcher.line_emoji import route_emoji

    counts: dict[str, int] = defaultdict(int)
    for candidates in stations.values():
        for cand in candidates:
            for line in cand["lines"]:
                counts[line] += 1
    unmapped = sorted(
        ((n, c) for n, c in counts.items() if route_emoji(n) is None),
        key=lambda nc: (-nc[1], nc[0]),
    )
    print(f"\n=== 絵文字未登録の路線 ({len(unmapped)} 本) ===", file=sys.stderr)
    for name, count in unmapped:
        print(f"  {count:4d} 駅  {name}", file=sys.stderr)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ekidata CSV → station_lines.json")
    parser.add_argument("--station", type=Path, required=True, help="ekidata station.csv")
    parser.add_argument("--line", type=Path, required=True, help="ekidata line.csv")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("src/jkkwatcher/data/station_lines.json"),
        help="出力 JSON パス",
    )
    parser.add_argument(
        "--pref",
        type=int,
        nargs="+",
        default=list(_KANTO_PREF_CDS),
        help="対象の pref_cd (既定: 関東 8-14)",
    )
    parser.add_argument(
        "--generated-at", default="", help="_meta.generated_at に入れる日付 (例 2026-06-11)"
    )
    parser.add_argument(
        "--report-unmapped",
        action="store_true",
        help="絵文字未登録の路線を出現駅数つきで出力",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    line_names = _load_line_names(args.line)
    stations = _build_stations(args.station, line_names, set(args.pref))

    n_candidates = sum(len(v) for v in stations.values())
    payload = {
        "_meta": {
            "source": "ekidata.jp",
            "schema_version": 1,
            "generated_at": args.generated_at,
            "pref_cds": sorted(args.pref),
            "station_names": len(stations),
            "candidates": n_candidates,
        },
        "stations": dict(sorted(stations.items())),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"[ok] {args.out} に駅 {len(stations)} 名 / 候補 {n_candidates} 件を書き出しました",
        file=sys.stderr,
    )

    if args.report_unmapped:
        _report_unmapped(stations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
