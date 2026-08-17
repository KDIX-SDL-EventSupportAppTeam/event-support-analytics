"""判断に直結する図表の生成。

仕様: docs/.sdd/05-visualization/chart-spec.md

「見て面白い図」ではなく「見た結果として設計が変わる図」だけを作る。
各図は decision-criteria.md の5つの判断のいずれかに紐づく。

出力先: output/figures/（PNG）。output/ はコミットしてよい。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402

import metrics  # noqa: E402

JST_LABEL = "(JST)"

# 日本語グリフ対応フォントが環境にあれば使う（無ければ既定フォントのまま、文字化けのみ発生）
for _font in ("Yu Gothic", "Meiryo", "Noto Sans CJK JP", "IPAexGothic", "Hiragino Sans"):
    if _font in {f.name for f in matplotlib.font_manager.fontManager.ttflist}:
        matplotlib.rcParams["font.family"] = _font
        break


def _save(fig, out_dir: Path, name: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / f"{name}.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def chart_a1_dwell_histogram(participants, day, stats, out_dir: Path) -> None:
    """A-1: 滞在時間ヒストグラム（判断1）。単発訪問者は除外し人数を注記。"""
    day_df = participants[participants["day"] == day]
    multi = day_df[day_df["is_single"] == False]  # noqa: E712
    dwell = multi["dwell_min"].dropna().clip(upper=180)  # 3時間以上は最上位ビンにまとめる

    fig, ax = plt.subplots(figsize=(8, 5))
    bins = list(range(0, 181, 30))
    ax.hist(dwell, bins=bins, edgecolor="white")
    ax.set_xlabel(f"滞在時間（分） {JST_LABEL}")
    ax.set_ylabel("人数")
    ax.set_title(f"A-1 滞在時間ヒストグラム（{day}、単発訪問者{stats['n_single']}名を除外）")
    pct = stats["pct_under_30min"]
    if pct is not None:
        ax.text(
            0.98, 0.95, f"30分未満: {pct:.1f}%",
            transform=ax.transAxes, ha="right", va="top",
            fontsize=13, bbox=dict(boxstyle="round", fc="white", ec="black"),
        )
    _save(fig, out_dir, f"A1_dwell_histogram_{day}")


def chart_c1_booth_count_histogram(participants, day, out_dir: Path) -> None:
    """C-1: 訪問ブース数ヒストグラム（判断3）。必ず0件から開始。"""
    day_df = participants[participants["day"] == day]
    max_n = int(day_df["n_booths"].max()) if len(day_df) else 0
    fig, ax = plt.subplots(figsize=(8, 5))
    bins = list(range(0, max_n + 2))
    ax.hist(day_df["n_booths"], bins=bins, edgecolor="white", align="left")
    ax.set_xlabel("訪問ブース数")
    ax.set_ylabel("人数")
    ax.set_title(f"C-1 訪問ブース数ヒストグラム（{day}、0件から表示）")
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    _save(fig, out_dir, f"C1_booth_count_histogram_{day}")


def chart_c3_dwell_vs_booths(participants, day, regression, out_dir: Path) -> None:
    """C-3: 滞在時間×訪問ブース数の散布図＋回帰直線（判断1・2）。"""
    day_df = participants[participants["day"] == day]
    multi = day_df[day_df["is_single"] == False]  # noqa: E712
    single = day_df[day_df["is_single"] == True]  # noqa: E712

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.scatter(single["dwell_min"], single["n_booths"], color="lightgray", s=15, label="単発訪問者（回帰除外）")
    ax.scatter(multi["dwell_min"], multi["n_booths"], s=15, label="複数訪問者")

    slope = regression["slope_booths_per_hour"]
    if slope is not None:
        x = multi["dwell_min"].dropna().sort_values()
        y = slope / 60 * x + regression["intercept"]
        ax.plot(x, y, color="black", linewidth=2)
        ax.text(
            0.02, 0.95, f"傾き: {slope:.2f} ブース/時間",
            transform=ax.transAxes, va="top",
            fontsize=12, bbox=dict(boxstyle="round", fc="white", ec="black"),
        )
    ax.set_xlabel("滞在時間（分）")
    ax.set_ylabel("訪問ブース数")
    ax.set_title(f"C-3 滞在時間×訪問ブース数（{day}）")
    ax.legend(loc="lower right", fontsize=9)
    _save(fig, out_dir, f"C3_dwell_vs_booths_{day}")


def chart_b2_b3_flow(ts_df, day, fallback_release_ts, out_dir: Path) -> None:
    """B-2/B-3: 新規参加者数（棒）と同時滞在人数（折れ線）の重ね描き（判断5）。"""
    fig, ax1 = plt.subplots(figsize=(10, 5))
    x = range(len(ts_df))
    labels = [t.strftime("%H:%M") for t in ts_df["bin"]]

    ax1.bar(x, ts_df["n_new_participants"], color="steelblue", alpha=0.6, label="新規参加者数(B-2)")
    ax1.set_xlabel(f"時刻 {JST_LABEL}")
    ax1.set_ylabel("新規参加者数", color="steelblue")
    ax1.set_xticks(list(x)[::2])
    ax1.set_xticklabels([labels[i] for i in x][::2], rotation=45, ha="right")

    ax2 = ax1.twinx()
    ax2.plot(x, ts_df["n_concurrent"], color="firebrick", linewidth=2, label="同時滞在人数(B-3)")
    ax2.set_ylabel("同時滞在人数", color="firebrick")

    if fallback_release_ts is not None:
        import pandas as pd

        target = pd.Timestamp(fallback_release_ts)
        for i, t in enumerate(ts_df["bin"]):
            if t >= target:
                ax1.axvline(i, color="green", linestyle="--", linewidth=1.5)
                ax1.text(i, ax1.get_ylim()[1] * 0.9, "推薦フォールバック解除", rotation=90, fontsize=8, color="green")
                break

    ax1.set_title(f"B-2/B-3 時間帯別の人の流れ（{day}）\n※B-3は初回チェックイン前・最終チェックイン後は不可視")
    _save(fig, out_dir, f"B2_B3_flow_{day}")


def chart_d1_booth_ranking(ranking, skew, out_dir: Path) -> None:
    """D-1: ブース別訪問者数（横棒、降順）＋D-3占有率（判断4）。"""
    top_n = skew["top20pct_n_booths"]
    fig, ax = plt.subplots(figsize=(8, max(6, len(ranking) * 0.22)))
    colors = ["firebrick" if i < top_n else "steelblue" for i in range(len(ranking))]
    ax.barh(ranking["booth_short"][::-1], ranking["n_visits"][::-1], color=colors[::-1])
    ax.set_xlabel("訪問者数")
    share = skew["top20pct_share"]
    ratio = skew["ratio_max_to_min"]
    title = f"D-1 ブース別訪問者数（上位20%={share:.1f}%占有" if share is not None else "D-1 ブース別訪問者数"
    if ratio is not None:
        title += f"、最多/最少={ratio:.1f}倍）"
    else:
        title += "）"
    ax.set_title(title)
    _save(fig, out_dir, "D1_booth_ranking")


def chart_e1_first_visit(first_visit_df, booths, day, out_dir: Path) -> None:
    """E-1: 初回訪問ブースの分布（上位10、判断は初回誘導の要否）。"""
    top = first_visit_df.merge(booths[["booth_id", "booth_short"]], on="booth_id", how="left").head(10)
    total = first_visit_df["n_first_visits"].sum()
    top3_share = first_visit_df.head(3)["n_first_visits"].sum() / total * 100 if total else 0

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.barh(top["booth_short"][::-1], top["n_first_visits"][::-1], color="darkorange")
    ax.set_xlabel("初回訪問者数")
    ax.set_title(f"E-1 初回訪問ブース上位10（{day}、上位3ブース占有率={top3_share:.1f}%）")
    _save(fig, out_dir, f"E1_first_visit_{day}")


def chart_cooldown_floor(floor_table, out_dir: Path) -> None:
    """クールタイムの床（交絡確認用、対数軸）。"""
    fig, ax = plt.subplots(figsize=(10, 5))
    x = range(len(floor_table))
    labels = [t.strftime("%m/%d %H:%M") for t in floor_table["bin"]]
    ax.plot(x, floor_table["min_sec"], marker="o", markersize=3, label="最小値")
    ax.plot(x, floor_table["p5_sec"], marker="x", markersize=3, label="5パーセンタイル")
    ax.set_yscale("log")
    ax.set_xticks(list(x)[::4])
    ax.set_xticklabels([labels[i] for i in x][::4], rotation=45, ha="right")
    ax.set_ylabel(f"チェックイン間隔（秒、対数軸） {JST_LABEL}")
    ax.set_title("クールタイムの床（交絡1の確認用。階段状の段差が変更時刻）")
    ax.legend()
    _save(fig, out_dir, "cooldown_floor")


def run_all(tables_dir: Path, out_dir: Path) -> None:
    visits, participants, booths = metrics.load_tables(tables_dir)

    floor_table = metrics.cooldown_floor(visits)
    if not floor_table.empty:
        chart_cooldown_floor(floor_table, out_dir)

    for day in (metrics.FRIDAY, metrics.SATURDAY):
        if participants[participants["day"] == day].empty:
            continue
        dwell_stats = metrics.dwell_time_stats(participants, day)
        chart_a1_dwell_histogram(participants, day, dwell_stats, out_dir)
        chart_c1_booth_count_histogram(participants, day, out_dir)

        regression = metrics.dwell_vs_booths_regression(participants, day)
        chart_c3_dwell_vs_booths(participants, day, regression, out_dir)

        ts_df = metrics.time_series(visits, participants, day)
        release_ts = metrics.recommendation_fallback_release_time(visits, day)
        chart_b2_b3_flow(ts_df, day, release_ts, out_dir)

        first_visit_df = metrics.first_visit_distribution(visits, day)
        if not first_visit_df.empty:
            chart_e1_first_visit(first_visit_df, booths, day, out_dir)

    ranking = metrics.booth_visit_ranking(visits, booths)
    skew = metrics.booth_skew_stats(ranking)
    chart_d1_booth_ranking(ranking, skew, out_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tables-dir", default="data/tables")
    parser.add_argument("--out-dir", default="output/figures")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_all(Path(args.tables_dir), Path(args.out_dir))
    print(f"wrote figures to {args.out_dir}")


if __name__ == "__main__":
    main()
