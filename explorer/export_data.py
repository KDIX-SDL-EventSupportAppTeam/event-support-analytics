"""ダンプを explorer/data/*.json に書き出す（値は加工しない）。

使い方:
    .venv/bin/python explorer/export_data.py ../db-archive/2026-10-16-prod-event/full   # fetch_full_dump.py の出力（推奨）
    python explorer/export_data.py ../db-archive/2026-10-16-prod-event/dump              # 旧 pkl ダンプ

- 各テーブルの全行・全列をそのまま JSON 配列にする
- 変換するのは型だけ: 欠損 → null、Timestamp → ISO 8601 文字列（保存時の値そのまま。TZ 補正なし）
- analysis.pkl は分析用の加工済みデータなので対象外
"""

import json
import math
import os
import pickle
import sys
from pathlib import Path

import pandas as pd

TABLES = [
    "users", "categories", "booths", "booth_tags", "awards",
    "check_ins", "booth_ratings", "award_votes",
    "bingo_cards", "bingo_cells", "card_unlock_events",
    "recommendation_scores", "user_survey_answers",
    "gacha_coin_uses",  # 取得できていれば読む（10/16 のダンプには無い）
]


def to_jsonable(v):
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    return v


def main_full(dump_dir: Path, out_dir: Path) -> None:
    import pyarrow.parquet as pq
    manifest = json.loads((dump_dir / "manifest.json").read_text(encoding="utf-8"))
    meta = {"source": str(dump_dir.resolve()), "fetched_at": manifest["fetched_at"], "tables": {}}
    for name, t in manifest["tables"].items():
        rows = pq.read_table(dump_dir / t["file"]).to_pylist()
        assert len(rows) == t["rows"], name
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        os.chmod(path, 0o600)
        meta["tables"][name] = {"rows": len(rows), "primary_key": t["primary_key"], "columns": t["columns"]}
        print(f"{name}: {len(rows)} rows")
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main(dump_dir: Path) -> None:
    out_dir = Path(__file__).parent / "data"
    out_dir.mkdir(exist_ok=True)
    os.chmod(out_dir, 0o700)
    for old in out_dir.glob("*.json"):
        old.unlink()
    if (dump_dir / "manifest.json").exists():
        return main_full(dump_dir, out_dir)
    meta = {"source": str(dump_dir.resolve()), "tables": {}}
    for name in TABLES:
        src = dump_dir / f"{name}.pkl"
        if not src.exists():
            print(f"skip {name}: not found")
            continue
        with open(src, "rb") as f:
            df = pickle.load(f)
        rows = [{k: to_jsonable(v) for k, v in r.items()} for r in df.to_dict("records")]
        assert len(rows) == len(df), name
        (out_dir / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        meta["tables"][name] = {
            "rows": len(rows),
            "columns": [{"name": c, "dtype": str(t)} for c, t in df.dtypes.items()],
        }
        print(f"{name}: {len(rows)} rows")
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
