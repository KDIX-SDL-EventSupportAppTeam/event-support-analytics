"""抽出済みの最新ダンプから、中間テーブル生成・指標算出・図表生成までを一括実行する。

Firestore へは接続しない（dump_firestore.py は別途手動実行する）。
"""

from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import build_tables
import metrics
import visualize


def latest_dump(raw_dir: Path) -> Path:
    candidates = sorted(glob.glob(str(raw_dir / "dump_*.json")))
    if not candidates:
        raise FileNotFoundError(f"no dump_*.json found under {raw_dir}")
    return Path(candidates[-1])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default="data/raw")
    parser.add_argument("--tables-dir", default="data/tables")
    parser.add_argument("--figures-dir", default="output/figures")
    parser.add_argument("--metrics-out", default="output/metrics.json")
    parser.add_argument("--exclude-pids", nargs="*", default=[])
    parser.add_argument("--stats-out", default="output/extraction_stats.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dump_path = latest_dump(Path(args.raw_dir))
    print(f"using {dump_path}")

    raw = build_tables.load_dump(dump_path)
    booths = build_tables.build_booths(raw)
    visits, stats = build_tables.build_visits(raw, booths)
    visits = build_tables.apply_staff_exclusion(visits, args.exclude_pids, stats)
    participants = build_tables.build_participants(raw, visits)

    tables_dir = Path(args.tables_dir)
    build_tables.write_csv(visits, tables_dir / "visits.csv")
    build_tables.write_csv(participants, tables_dir / "participants.csv")
    build_tables.write_csv(booths, tables_dir / "booths.csv")
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))

    stats_out = Path(args.stats_out)
    stats_out.parent.mkdir(parents=True, exist_ok=True)
    record = {"dump_path": str(dump_path),
              "generated_at": datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds"),
              **stats}
    with stats_out.open("w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2, default=str)
    print(f"wrote {stats_out}")

    result = metrics.run_all(tables_dir)
    metrics_out = Path(args.metrics_out)
    metrics_out.parent.mkdir(parents=True, exist_ok=True)
    with metrics_out.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"wrote {metrics_out}")

    visualize.run_all(tables_dir, Path(args.figures_dir))
    print(f"wrote figures to {args.figures_dir}")


if __name__ == "__main__":
    main()
