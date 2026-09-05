"""生データ(JSON) から中間テーブル(visits / participants / booths)を生成する。

仕様: docs/.sdd/04-analysis/intermediate-tables.md
除外規則: docs/.sdd/03-extraction/exclusion-rules.md

除外は抽出時ではなくここで行う。生データ自体は無加工のまま保持する。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

JST = ZoneInfo("Asia/Tokyo")
UTC = ZoneInfo("UTC")

EVENT_START_JST = pd.Timestamp("2025-10-10 00:00:00", tz=JST)
EVENT_END_JST = pd.Timestamp("2025-10-11 23:59:59", tz=JST)

# 4x4 grid: 10 winning lines (4 rows, 4 cols, 2 diagonals) over positions 0-15
BINGO_LINES = (
    [[r * 4 + c for c in range(4)] for r in range(4)]  # rows
    + [[r * 4 + c for r in range(4)] for c in range(4)]  # cols
    + [[0, 5, 10, 15], [3, 6, 9, 12]]  # diagonals
)


def to_jst(ts_utc: str | None) -> pd.Timestamp | None:
    if not ts_utc:
        return None
    return pd.Timestamp(ts_utc).tz_convert(JST)


def load_dump(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def build_booths(raw: dict) -> pd.DataFrame:
    booths = pd.DataFrame(raw["booths"])
    booths = booths.sort_values("booth_no").reset_index(drop=True)

    def short_name(row) -> str:
        name = row["booth_name"] or ""
        summary = name if len(name) <= 20 else name[:19] + "…"
        return f"{row['booth_id']} {summary}"

    booths["booth_short"] = booths.apply(short_name, axis=1)
    booths["map_x"] = pd.NA
    booths["map_y"] = pd.NA
    booths["zone"] = pd.NA
    return booths[
        [
            "booth_id",
            "booth_no",
            "booth_name",
            "booth_short",
            "booth_description",
            "booth_emoji",
            "map_x",
            "map_y",
            "zone",
        ]
    ]


def build_visits(raw: dict, booths: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    checkins = pd.DataFrame(raw["checkins"])
    stats = {"raw_checkins": len(checkins)}

    checkins["ts_jst"] = checkins["ts_utc"].map(to_jst)
    booth_no_map = booths.set_index("booth_id")["booth_no"]
    checkins["booth_no"] = checkins["booth_id"].map(booth_no_map)

    # 規則1: イベント期間外を除外（機械的）
    in_range = checkins["ts_jst"].between(EVENT_START_JST, EVENT_END_JST)
    excluded = checkins[~in_range]
    stats["excluded_out_of_period"] = len(excluded)
    stats["excluded_out_of_period_users"] = excluded["pid"].nunique()
    visits = checkins[in_range].copy()

    visits["day"] = visits["ts_jst"].dt.date

    visits = visits.sort_values(["pid", "day", "ts_jst"])
    visits["visit_seq"] = visits.groupby(["pid", "day"]).cumcount() + 1
    prev_ts = visits.groupby(["pid", "day"])["ts_jst"].shift(1)
    visits["gap_min"] = (visits["ts_jst"] - prev_ts).dt.total_seconds() / 60
    visits.loc[visits["visit_seq"] == 1, "gap_min"] = pd.NA

    stats["visits_in_period"] = len(visits)
    stats["visits_in_period_users"] = visits["pid"].nunique()

    return visits[
        ["pid", "booth_id", "booth_no", "ts_jst", "day", "visit_seq", "gap_min"]
    ].reset_index(drop=True), stats


def _bingo_lines_completed(hit_positions: set[int]) -> int:
    return sum(1 for line in BINGO_LINES if set(line).issubset(hit_positions))


def build_participants(raw: dict, visits: pd.DataFrame) -> pd.DataFrame:
    users = pd.DataFrame(raw["users"]).set_index("pid")
    bingo = pd.DataFrame(raw["bingo_card"])
    awards = pd.DataFrame(raw["awards"])
    user_status = pd.DataFrame(raw["user_status"])

    voted_pids = set(awards["pid"]) if not awards.empty else set()
    finalized_pids = set(
        user_status.loc[user_status["vote_finalized"] == 1, "pid"]
    ) if not user_status.empty else set()

    rows = []

    for pid, user_visits in visits.groupby("pid"):
        card = bingo[bingo["pid"] == pid]
        n_card = len(card)
        rec_card = card[card["is_recommendation"] == True]  # noqa: E712
        n_rec_total = len(rec_card)
        rec_positions = set(rec_card["position"])

        # ビンゴカードは日をまたいで持ち越される。ライン成立は当日分だけでなく
        # その日までに踏んだマスの累積で判定する（日ごとにリセットされない）。
        cumulative_positions: set[int] = set()

        for day, g in user_visits.groupby("day"):
            g = g.sort_values("ts_jst")
            first_ts, last_ts = g["ts_jst"].iloc[0], g["ts_jst"].iloc[-1]
            n_booths = len(g)
            dwell_min = (last_ts - first_ts).total_seconds() / 60

            checked_booths = set(g["booth_id"])
            hit_positions = set(card.loc[card["booth_id"].isin(checked_booths), "position"])
            # 同一ブースへの再チェックインは構造上存在しないため、日ごとのヒットは
            # 互いに素。よって n_card_hit / n_rec_hit は日をまたいで合算してよい。
            n_card_hit = len(hit_positions)
            n_rec_hit = len(rec_positions & hit_positions)

            cumulative_positions |= hit_positions
            bingo_lines = _bingo_lines_completed(cumulative_positions)

            rows.append(
                {
                    "pid": pid,
                    "day": day,
                    "age": users.loc[pid, "age"] if pid in users.index else None,
                    "gender": users.loc[pid, "gender"] if pid in users.index else None,
                    "genre": users.loc[pid, "genre"] if pid in users.index else None,
                    "first_ts": first_ts,
                    "last_ts": last_ts,
                    "dwell_min": dwell_min,
                    "n_booths": n_booths,
                    "is_single": n_booths == 1,
                    "n_card": n_card,
                    "n_card_hit": n_card_hit,
                    "n_rec_hit": n_rec_hit,
                    "n_rec_total": n_rec_total,
                    "bingo_lines": bingo_lines,
                    "voted": pid in voted_pids,
                    "vote_finalized": pid in finalized_pids,
                }
            )

    # チェックイン0件ユーザーも1行だけ作る（アプリを使わなかった層を消さない）
    active_pids = set(visits["pid"])
    for pid in users.index:
        if pid in active_pids:
            continue
        card = bingo[bingo["pid"] == pid]
        rows.append(
            {
                "pid": pid,
                "day": None,
                "age": users.loc[pid, "age"],
                "gender": users.loc[pid, "gender"],
                "genre": users.loc[pid, "genre"],
                "first_ts": None,
                "last_ts": None,
                "dwell_min": None,
                "n_booths": 0,
                "is_single": False,
                "n_card": len(card),
                "n_card_hit": 0,
                "n_rec_hit": 0,
                "n_rec_total": len(card[card["is_recommendation"] == True]),  # noqa: E712
                "bingo_lines": 0,
                "voted": pid in voted_pids,
                "vote_finalized": pid in finalized_pids,
            }
        )

    return pd.DataFrame(rows)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


STAFF_BOOTH_NO = 38  # PRoToFES BINGO（制作チーム自身の出展ブース）
STAFF_MAX_BOOTHS_THRESHOLD = 30


def detect_staff_candidates(
    visits: pd.DataFrame, booths: pd.DataFrame, open_hour_jst: int = 9
) -> pd.DataFrame:
    """規則2の手がかりに基づく候補一覧。自動除外はしない（目視確定用）。"""
    staff_booth_ids = set(booths.loc[booths["booth_no"] == STAFF_BOOTH_NO, "booth_id"])
    rows = []
    for pid, g in visits.groupby("pid"):
        g = g.sort_values("ts_jst")
        first = g.iloc[0]
        reasons = []
        if first["ts_jst"].hour < open_hour_jst:
            reasons.append("開場前チェックイン")
        if len(g) >= STAFF_MAX_BOOTHS_THRESHOLD:
            reasons.append(f"チェックイン{len(g)}件（閾値{STAFF_MAX_BOOTHS_THRESHOLD}以上）")
        staff_hits = g[g["booth_id"].isin(staff_booth_ids)]
        if not staff_hits.empty and staff_hits["ts_jst"].min().hour < open_hour_jst + 1:
            reasons.append("開場直後の推薦チーム出展ブースへのチェックイン")
        if reasons:
            rows.append(
                {
                    "pid": pid,
                    "n_booths": len(g),
                    "first_ts": first["ts_jst"],
                    "reasons": "; ".join(reasons),
                }
            )
    return pd.DataFrame(rows)


def apply_staff_exclusion(visits: pd.DataFrame, exclude_pids: list[str], stats: dict) -> pd.DataFrame:
    """規則2で目視確定した pid を visits から外し、**件数と pid を stats に残す**。

    記録が無いと、後から「何を除外して出した数字か」が辿れない（FINDINGS 付記の事故）。
    """
    pids = sorted({p.strip() for p in exclude_pids if p and p.strip()})
    stats["excluded_pids"] = pids
    stats["excluded_staff_users"] = len(pids)
    if not pids:
        stats["visits_after_staff_exclusion_users"] = int(visits["pid"].nunique())
        return visits
    kept = visits[~visits["pid"].isin(pids)]
    stats["visits_after_staff_exclusion_users"] = int(kept["pid"].nunique())
    return kept


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump_path", help="data/raw/dump_*.json へのパス")
    parser.add_argument("--out-dir", default="data/tables")
    parser.add_argument(
        "--exclude-pids",
        nargs="*",
        default=[],
        help="規則2（運営・出展者）に基づき目視で確定した除外対象の pid",
    )
    parser.add_argument(
        "--show-staff-candidates",
        action="store_true",
        help="規則2の除外候補を出力して終了する（自動除外はしない）",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw = load_dump(Path(args.dump_path))

    booths = build_booths(raw)
    visits, stats = build_visits(raw, booths)

    if args.show_staff_candidates:
        candidates = detect_staff_candidates(visits, booths)
        print(candidates.to_string(index=False) if not candidates.empty else "候補なし")
        return

    before = visits["pid"].nunique()
    visits = apply_staff_exclusion(visits, args.exclude_pids, stats)
    if args.exclude_pids:
        print(f"excluded {before - visits['pid'].nunique()} staff/exhibitor users")

    participants = build_participants(raw, visits)

    out_dir = Path(args.out_dir)
    write_csv(visits, out_dir / "visits.csv")
    write_csv(participants, out_dir / "participants.csv")
    write_csv(booths, out_dir / "booths.csv")

    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    print(f"wrote tables to {out_dir}")


if __name__ == "__main__":
    main()
