"""指標カタログの算出。

仕様: docs/.sdd/04-analysis/metrics-catalog.md
交絡の統制: docs/.sdd/04-analysis/confounders.md

算出順序（仕様どおり）:
1. クールタイムの床の検出（交絡1）
2. 日別の基礎集計（F-1）
3. A（滞在時間）
4. C-1 / C-3（訪問ブース数）
5. B（時間帯）
6. D-1〜D-3（偏り）
7. E-1（初回訪問）
8. 推薦マスの効果測定
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

FRIDAY = pd.Timestamp("2025-10-10").date()
SATURDAY = pd.Timestamp("2025-10-11").date()
RECOMMENDATION_FALLBACK_THRESHOLD = 20  # チェックイン実績者がこの人数を超えたらフォールバック解除


def load_tables(tables_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    visits = pd.read_csv(tables_dir / "visits.csv", parse_dates=["ts_jst"])
    participants = pd.read_csv(tables_dir / "participants.csv", parse_dates=["first_ts", "last_ts"])
    booths = pd.read_csv(tables_dir / "booths.csv")
    visits["day"] = pd.to_datetime(visits["day"]).dt.date
    participants["day"] = pd.to_datetime(participants["day"]).dt.date
    return visits, participants, booths


# --- 交絡1: クールタイムの床の検出 -----------------------------------------


def cooldown_floor(visits: pd.DataFrame, bin_minutes: int = 30) -> pd.DataFrame:
    """30分ビンごとの gap_min の最小値・5パーセンタイル（秒）。"""
    df = visits.dropna(subset=["gap_min"]).copy()
    df["gap_sec"] = df["gap_min"] * 60
    df["bin"] = df["ts_jst"].dt.floor(f"{bin_minutes}min")
    agg = df.groupby("bin")["gap_sec"].agg(min_sec="min", p5_sec=lambda s: s.quantile(0.05), n="count")
    return agg.reset_index()


def cooldown_saturation(visits: pd.DataFrame, floor_table: pd.DataFrame, tolerance: float = 0.2) -> float:
    """全チェックイン間隔のうち、その時点の床から+20%以内に収まっている割合。"""
    df = visits.dropna(subset=["gap_min"]).copy()
    df["gap_sec"] = df["gap_min"] * 60
    df["bin"] = df["ts_jst"].dt.floor("30min")
    merged = df.merge(floor_table[["bin", "min_sec"]], on="bin", how="left")
    within = merged["gap_sec"] <= merged["min_sec"] * (1 + tolerance)
    return float(within.mean()) if len(merged) else float("nan")


# --- F-1: 日別基礎集計 ------------------------------------------------------


def daily_summary(participants: pd.DataFrame) -> pd.DataFrame:
    active = participants.dropna(subset=["day"])
    by_day = active.groupby("day").agg(
        n_participants=("pid", "nunique"),
        n_checkins=("n_booths", "sum"),
    )
    both_days = set(active[active["day"] == FRIDAY]["pid"]) & set(active[active["day"] == SATURDAY]["pid"])
    zero_checkin = participants["day"].isna().sum()
    return {
        "by_day": by_day.reset_index().to_dict(orient="records"),
        "n_both_days": len(both_days),
        "n_zero_checkin": int(zero_checkin),
        "n_card_ungenerated": int((participants.groupby("pid")["n_card"].max() == 0).sum()),
    }


# --- A: 滞在時間 -------------------------------------------------------------


def dwell_time_stats(participants: pd.DataFrame, day) -> dict:
    day_df = participants[participants["day"] == day]
    multi = day_df[day_df["is_single"] == False]  # noqa: E712
    dwell = multi["dwell_min"].dropna()
    under_30 = day_df["dwell_min"].dropna()  # includes single-visit (dwell=0) in denominator
    return {
        "day": str(day),
        "median": float(dwell.median()) if len(dwell) else None,
        "mean": float(dwell.mean()) if len(dwell) else None,
        "q1": float(dwell.quantile(0.25)) if len(dwell) else None,
        "q3": float(dwell.quantile(0.75)) if len(dwell) else None,
        "n_single": int((day_df["is_single"] == True).sum()),  # noqa: E712
        "pct_single": float((day_df["is_single"] == True).mean() * 100) if len(day_df) else None,  # noqa: E712
        "pct_under_30min": float((under_30 < 30).mean() * 100) if len(under_30) else None,
        "pct_over_3h": float((dwell >= 180).mean() * 100) if len(dwell) else None,
    }


# --- C: 訪問ブース数 ---------------------------------------------------------


def booth_count_stats(participants: pd.DataFrame, day) -> dict:
    day_df = participants[participants["day"] == day]
    return {
        "day": str(day),
        "median_n_booths": float(day_df["n_booths"].median()) if len(day_df) else None,
        "mean_n_booths": float(day_df["n_booths"].mean()) if len(day_df) else None,
    }


def dwell_vs_booths_regression(participants: pd.DataFrame, day) -> dict:
    """C-3: 単発訪問者を除外した回帰。傾き = ブース/分。"""
    day_df = participants[(participants["day"] == day) & (participants["is_single"] == False)]  # noqa: E712
    x = day_df["dwell_min"].dropna()
    y = day_df.loc[x.index, "n_booths"]
    if len(x) < 2:
        return {"day": str(day), "slope_booths_per_hour": None, "intercept": None, "n": len(x)}
    slope, intercept = np.polyfit(x, y, 1)
    return {
        "day": str(day),
        "slope_booths_per_hour": float(slope * 60),
        "intercept": float(intercept),
        "n": int(len(x)),
    }


def per_user_card_stats(participants: pd.DataFrame) -> pd.DataFrame:
    """カード関連の指標を「参加者×日」から「参加者」単位に畳み込む。

    participants は (pid, day) が1行のため、同一カードを指す n_card / n_rec_total は
    両日参加者で重複する。集計時は必ずこの関数を通すこと。

    - n_card / n_rec_total … 全日で同じ値なので max（= 1回だけ数える）
    - n_card_hit / n_rec_hit … 同一ブースへの再チェックインが存在せず日ごとのヒットは
      互いに素なので sum
    - bingo_lines … build_tables 側で累積判定済みなので max（最終日の値が全期間の値）
    """
    return participants.groupby("pid").agg(
        n_card=("n_card", "max"),
        n_rec_total=("n_rec_total", "max"),
        n_card_hit=("n_card_hit", "sum"),
        n_rec_hit=("n_rec_hit", "sum"),
        bingo_lines=("bingo_lines", "max"),
    )


def bingo_completion(participants: pd.DataFrame) -> dict:
    by_user = per_user_card_stats(participants)
    return {
        "median_n_card_hit": float(by_user["n_card_hit"].median()),
        "pct_completed_4_lines": float((by_user["bingo_lines"] >= 4).mean() * 100),
        "n_users": int(len(by_user)),
    }


# --- B: 時間帯別 --------------------------------------------------------------


def time_series(visits: pd.DataFrame, participants: pd.DataFrame, day, bin_minutes: int = 30) -> pd.DataFrame:
    day_visits = visits[visits["day"] == day].copy()
    day_visits["bin"] = day_visits["ts_jst"].dt.floor(f"{bin_minutes}min")
    b1 = day_visits.groupby("bin").size().rename("n_checkins")

    day_participants = participants[participants["day"] == day].dropna(subset=["first_ts"])
    first_bin = day_participants["first_ts"].dt.floor(f"{bin_minutes}min")
    b2 = first_bin.value_counts().rename("n_new_participants").sort_index()

    bins = b1.index.union(b2.index)
    concurrent = []
    for t in bins:
        mask = (day_participants["first_ts"] <= t) & (day_participants["last_ts"] >= t)
        concurrent.append(mask.sum())
    b3 = pd.Series(concurrent, index=bins, name="n_concurrent")

    out = pd.DataFrame({"bin": bins}).set_index("bin")
    out = out.join([b1, b2, b3]).fillna(0).reset_index()
    return out.sort_values("bin")


def recommendation_fallback_release_time(visits: pd.DataFrame, day) -> str | None:
    """チェックイン実績者数が20人に到達した時刻（= 20人目の初回チェックイン時刻）。"""
    day_visits = visits[visits["day"] == day].sort_values("ts_jst")
    first_seen = day_visits.drop_duplicates("pid", keep="first").sort_values("ts_jst")
    if len(first_seen) < RECOMMENDATION_FALLBACK_THRESHOLD:
        return None
    row = first_seen.iloc[RECOMMENDATION_FALLBACK_THRESHOLD - 1]
    return row["ts_jst"].isoformat()


# --- D: ブース別偏り ----------------------------------------------------------


def booth_visit_ranking(visits: pd.DataFrame, booths: pd.DataFrame) -> pd.DataFrame:
    counts = visits.groupby("booth_id").size().rename("n_visits")
    ranked = booths.merge(counts, on="booth_id", how="left").fillna({"n_visits": 0})
    ranked["n_visits"] = ranked["n_visits"].astype(int)
    return ranked.sort_values("n_visits", ascending=False).reset_index(drop=True)


def booth_skew_stats(ranking: pd.DataFrame) -> dict:
    # min_v / ratio は訪問者0のブースも含めた全40ブースから算出する。
    # 0件のブースが存在する場合、倍率は定義できないため差で報告する（D-2の注記）。
    max_v = ranking["n_visits"].max()
    min_v = ranking["n_visits"].min()
    top_n = max(1, round(len(ranking) * 0.2))
    top_share = ranking.head(top_n)["n_visits"].sum() / ranking["n_visits"].sum() if ranking["n_visits"].sum() else None
    return {
        "max_visits": int(max_v),
        "min_visits": int(min_v),
        "ratio_max_to_min": (float(max_v / min_v) if min_v > 0 else None),
        "diff_max_to_min": int(max_v - min_v),
        "top20pct_share": float(top_share * 100) if top_share is not None else None,
        "top20pct_n_booths": int(top_n),
    }


# --- E-1: 初回訪問ブース -------------------------------------------------------


def first_visit_distribution(visits: pd.DataFrame, day) -> pd.DataFrame:
    firsts = visits[(visits["day"] == day) & (visits["visit_seq"] == 1)]
    return firsts.groupby("booth_id").size().rename("n_first_visits").sort_values(ascending=False).reset_index()


# --- 推薦マスの効果測定 ---------------------------------------------------------


def recommendation_effect(participants: pd.DataFrame) -> dict:
    by_user = per_user_card_stats(participants)  # 両日参加者の分母重複を排除する
    rec_hit = by_user["n_rec_hit"].sum()
    rec_total = by_user["n_rec_total"].sum()
    card_hit = by_user["n_card_hit"].sum()
    card_total = by_user["n_card"].sum()
    random_hit = card_hit - rec_hit
    random_total = card_total - rec_total
    return {
        "recommended_hit_rate": float(rec_hit / rec_total) if rec_total else None,
        "random_hit_rate": float(random_hit / random_total) if random_total else None,
        "n_recommended_slots": int(rec_total),
        "n_random_slots": int(random_total),
        "caveat": "推薦マスは中央4マス固定のため、位置効果と推薦効果が交絡している",
    }


# --- F-4: 投票確定率 -----------------------------------------------------------


def vote_finalization_rate(participants: pd.DataFrame) -> dict:
    by_pid = participants.groupby("pid")["vote_finalized"].max()
    return {"vote_finalized_rate_pct": float(by_pid.mean() * 100), "n": int(len(by_pid))}


def run_all(tables_dir: Path) -> dict:
    visits, participants, booths = load_tables(tables_dir)

    floor_table = cooldown_floor(visits)
    saturation = cooldown_saturation(visits, floor_table)

    result = {
        "cooldown": {
            "saturation_within_20pct_of_floor": saturation,
            "interpretation": (
                "20-30%以上ならE-2は下限値としてのみ使用可、E-3は結論を出さない"
                if (saturation is not None and not np.isnan(saturation) and saturation >= 0.2)
                else "数%程度ならクールタイムは無視してよい"
            ),
        },
        "f1_daily_summary": daily_summary(participants),
        "a_dwell_time": {
            "friday": dwell_time_stats(participants, FRIDAY),
            "saturday": dwell_time_stats(participants, SATURDAY),
        },
        "c_booth_count": {
            "friday": booth_count_stats(participants, FRIDAY),
            "saturday": booth_count_stats(participants, SATURDAY),
            "regression_friday": dwell_vs_booths_regression(participants, FRIDAY),
            "bingo_completion": bingo_completion(participants),
        },
        "b_fallback_release": {
            "friday": recommendation_fallback_release_time(visits, FRIDAY),
            "saturday": recommendation_fallback_release_time(visits, SATURDAY),
        },
        "d_booth_skew": booth_skew_stats(booth_visit_ranking(visits, booths)),
        "recommendation_effect": recommendation_effect(participants),
        "vote_finalization": vote_finalization_rate(participants),
    }
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tables-dir", default="data/tables")
    parser.add_argument("--out", default="output/metrics.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_all(Path(args.tables_dir))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"wrote {out_path}")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
