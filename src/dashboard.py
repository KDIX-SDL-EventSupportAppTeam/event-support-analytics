"""ローカルGUIダッシュボード（Streamlit）。

起動:
    streamlit run src/dashboard.py

data/tables/ を読むだけで、個人データを含むファイルを新たに生成しない。
指標の定義は metrics.py を経由し、ここでは再実装しない（仕様の二重管理を避ける）。

判断基準は docs/.sdd/01-context/decision-criteria.md で事前に固定されている。
本ダッシュボードは絞り込みの結果を、そのしきい値に自動で当てはめて表示する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import metrics  # noqa: E402

TABLES_DIR = Path("data/tables")
MIN_CELL_SIZE = 3  # 該当者がこれ未満の絞り込みは集計を表示しない（privacy-policy.md）

st.set_page_config(page_title="プロトフェス行動データ", layout="wide")


# --- データ読み込み -----------------------------------------------------------


@st.cache_data
def load(tables_dir: str):
    visits, participants, booths = metrics.load_tables(Path(tables_dir))
    return visits, participants, booths


def multiselect_all(label: str, series: pd.Series):
    """欠損を除いたユニーク値の複数選択。既定は全選択。"""
    options = sorted(str(v) for v in series.dropna().unique())
    return st.sidebar.multiselect(label, options, default=options)


def verdict(box, text: str, level: str) -> None:
    {"warn": box.error, "mid": box.warning, "ok": box.success}[level](text)


# --- 本体 ---------------------------------------------------------------------


def main() -> None:
    st.title("第3回プロトフェス 来場者行動データ")

    if not (TABLES_DIR / "participants.csv").exists():
        st.error(
            f"`{TABLES_DIR}` に中間テーブルがありません。\n\n"
            "先に `python src/dump_firestore.py` と `python src/run_pipeline.py` を実行してください。"
        )
        st.stop()

    visits, participants, booths = load(str(TABLES_DIR))

    # === サイドバー: 絞り込み ===
    st.sidebar.header("絞り込み")

    day_choice = st.sidebar.radio(
        "開催日",
        ["両日", "金曜 (10/10)", "土曜 (10/11)"],
        help=(
            "主分析は原則として初日（金）。ただしクールタイム値が一定なのは土曜のため、"
            "チェックイン間隔に依存する指標（E-2/E-3）は土曜を主とする。"
        ),
    )
    day = {"両日": None, "金曜 (10/10)": metrics.FRIDAY, "土曜 (10/11)": metrics.SATURDAY}[day_choice]

    ages = multiselect_all("年代", participants["age"])
    genders = multiselect_all("性別", participants["gender"])
    genres = multiselect_all("興味ジャンル", participants["genre"])

    st.sidebar.divider()
    include_zero = st.sidebar.checkbox(
        "チェックイン0件の参加者を含める",
        value=True,
        help="「アプリを使わなかった層」。判断3に直結するため既定で含める",
    )
    include_single = st.sidebar.checkbox("単発訪問者（1ブースのみ）を含める", value=True)

    exclude_raw = st.sidebar.text_area(
        "除外する pid（運営・出展者。1行1件）",
        value="",
        help="除外規則2は自動判定しない。「除外候補」タブで確認してから指定する",
    )
    exclude_pids = {p.strip() for p in exclude_raw.splitlines() if p.strip()}

    # === 絞り込みの適用 ===
    p = participants.copy()
    if exclude_pids:
        p = p[~p["pid"].isin(exclude_pids)]
    p = p[p["age"].astype(str).isin(ages) | p["age"].isna()]
    p = p[p["gender"].astype(str).isin(genders) | p["gender"].isna()]
    p = p[p["genre"].astype(str).isin(genres) | p["genre"].isna()]

    zero_rows = p[p["day"].isna()]
    active = p.dropna(subset=["day"])
    if day is not None:
        active = active[active["day"] == day]
    if not include_single:
        active = active[active["is_single"] == False]  # noqa: E712
    p = pd.concat([active, zero_rows]) if include_zero else active

    keys = set(zip(active["pid"], active["day"]))
    v = visits[[(a, b) in keys for a, b in zip(visits["pid"], visits["day"])]]

    # === プライバシー保護: 小セル抑制 ===
    n_people = active["pid"].nunique()
    if n_people < MIN_CELL_SIZE:
        st.warning(
            f"絞り込みの結果、該当者が {n_people} 名になりました。\n\n"
            f"{MIN_CELL_SIZE} 名未満の集計は、属性の組み合わせから個人が推定されうるため表示しません"
            "（docs/.sdd/03-extraction/privacy-policy.md）。条件を緩めてください。"
        )
        st.stop()

    dwell = metrics.dwell_time_stats(active)
    counts = metrics.booth_count_stats(active)

    # === KPI ===
    st.subheader(f"概要（{day_choice}）")
    c = st.columns(5)
    c[0].metric("参加者（延べ）", f"{len(active)}")
    c[1].metric("実人数", f"{n_people}")
    c[2].metric("チェックイン", f"{int(active['n_booths'].sum())}")
    c[3].metric(
        "滞在時間の中央値",
        f"{dwell['median']:.0f} 分" if dwell["median"] is not None else "—",
        help="単発訪問者を除外して算出（交絡3）",
    )
    c[4].metric(
        "訪問ブース数の中央値",
        f"{counts['median_n_booths']:.1f}" if counts["median_n_booths"] is not None else "—",
    )
    st.caption(
        "滞在時間は「最終チェックイン − 初回チェックイン」による代用値であり、"
        "**真の滞在時間の下限**である（受付〜初回、最終〜退場のラグが失われるため）。"
    )

    tabs = st.tabs(
        [
            "判断1 参加者の典型像",
            "判断2 データ件数規模",
            "判断3 推薦が届く対象",
            "判断4 ブースの偏り",
            "判断5 溜まるタイミング",
            "推薦マスの効果",
            "前提: クールタイム",
            "除外候補",
        ]
    )

    # --- 判断1 ---
    with tabs[0]:
        pct = dwell["pct_under_30min"]
        if pct is None:
            st.info("該当データがありません。")
        else:
            st.markdown(f"### 30分未満の割合: **{pct:.1f}%**")
            if pct >= 50:
                verdict(st, "短時間層が多数派。推薦アルゴリズムの精緻化より「短時間滞在者をどう扱うか」が主論点。", "warn")
            elif pct >= 20:
                verdict(st, "二層構造。短時間層と長時間層で別々の体験設計が要る。", "mid")
            else:
                verdict(st, "長時間滞在が標準。推薦の作り込みに投資する価値がある。", "ok")

            multi = active[active["is_single"] == False]  # noqa: E712
            d = multi["dwell_min"].dropna().clip(upper=180)
            fig = go.Figure(go.Histogram(x=d, xbins=dict(start=0, end=180, size=30)))
            fig.update_layout(
                title=f"A-1 滞在時間ヒストグラム（{day_choice}、単発訪問者 {dwell['n_single']} 名を除外／最上位ビンは3時間以上）",
                xaxis_title="滞在時間（分）",
                yaxis_title="人数",
                bargap=0.05,
            )
            st.plotly_chart(fig, width="stretch")

            st.caption(
                f"中央値 {dwell['median']:.0f}分 ／ 平均 {dwell['mean']:.0f}分 ／ "
                f"Q1 {dwell['q1']:.0f}分 ／ Q3 {dwell['q3']:.0f}分 ／ "
                f"3時間以上 {dwell['pct_over_3h']:.1f}%"
                if dwell["median"] is not None
                else ""
            )

            reg = metrics.dwell_vs_booths_regression(active)
            fig2 = go.Figure()
            single = active[active["is_single"] == True]  # noqa: E712
            fig2.add_scatter(
                x=single["dwell_min"], y=single["n_booths"], mode="markers",
                name="単発訪問者（回帰除外）", marker=dict(color="lightgray", size=6),
            )
            fig2.add_scatter(
                x=multi["dwell_min"], y=multi["n_booths"], mode="markers",
                name="複数訪問者", marker=dict(size=6),
            )
            slope = reg["slope_booths_per_hour"]
            if slope is not None:
                xs = np.linspace(multi["dwell_min"].min(), multi["dwell_min"].max(), 50)
                fig2.add_scatter(
                    x=xs, y=slope / 60 * xs + reg["intercept"], mode="lines",
                    name=f"回帰: {slope:.2f} ブース/時間", line=dict(color="black", width=2),
                )
            fig2.update_layout(
                title=f"C-3 滞在時間 × 訪問ブース数（{day_choice}）",
                xaxis_title="滞在時間（分）", yaxis_title="訪問ブース数",
            )
            st.plotly_chart(fig2, width="stretch")
            if slope is not None:
                st.info(f"傾き **{slope:.2f} ブース/時間**。今年の規模見積もりで最も再利用される数字。")

    # --- 判断2 ---
    with tabs[1]:
        st.markdown("### 今年の想定チェックイン数")
        expected = st.slider("今年の想定参加者数", 50, 400, 175, step=25)
        median_booths = counts["median_n_booths"]
        if median_booths is None:
            st.info("該当データがありません。")
        else:
            est = expected * median_booths
            st.markdown(f"#### {expected} 名 × 中央値 {median_booths:.1f} ブース = **{est:.0f} 件**")
            if est >= 1000:
                verdict(st, "協調フィルタリングが機能する程度のデータ量。推薦の作り込みは妥当。", "ok")
            elif est >= 300:
                verdict(st, "限界的。単純な人気度ベースの推薦に留めるべき。", "mid")
            else:
                verdict(st, "個人化推薦は成立しない。ルールベースで設計する。", "warn")
            if day is None:
                st.warning(
                    "**両日を選択中。** 去年の総数は2日間の延べ値であり、今年（金曜1日）の見積もりと"
                    "直接比較してはならない。サイドバーで金曜に絞ってから読むこと。"
                )

        summary = metrics.daily_summary(p)
        st.markdown("#### F-1 日別の基礎集計")
        st.dataframe(pd.DataFrame(summary["by_day"]), width="stretch")
        cc = st.columns(3)
        cc[0].metric("両日参加者", summary["n_both_days"])
        cc[1].metric("チェックイン0件", summary["n_zero_checkin"])
        cc[2].metric("カード未生成", summary["n_card_ungenerated"])

    # --- 判断3 ---
    with tabs[2]:
        n_zero = len(zero_rows)
        n_single = int((active["is_single"] == True).sum())  # noqa: E712
        denom = n_people + n_zero
        if denom == 0:
            st.info("該当データがありません。")
        else:
            share = (n_zero + n_single) / denom * 100
            st.markdown(
                f"### 推薦が構造的に届かない層: **{share:.1f}%**"
                f"（0件 {n_zero} 名 + 単発 {n_single} 名 / {denom} 名）"
            )
            if share >= 40:
                verdict(st, "推薦機能より先に「使われない問題」を解くべき。オンボーディング設計が優先。", "warn")
            elif share >= 20:
                verdict(st, "推薦は有効だが、初回体験の改善と並行して進める。", "mid")
            else:
                verdict(st, "推薦機能に集中してよい。", "ok")
            if not include_zero:
                st.warning("チェックイン0件の参加者を除外中のため、この割合は過小評価されている。")

            hist = pd.concat([active["n_booths"], pd.Series([0] * n_zero)]) if include_zero else active["n_booths"]
            fig = go.Figure(go.Histogram(x=hist, xbins=dict(start=-0.5, end=hist.max() + 0.5, size=1)))
            fig.update_layout(
                title=f"C-1 訪問ブース数ヒストグラム（{day_choice}、必ず0件から表示）",
                xaxis_title="訪問ブース数", yaxis_title="人数", bargap=0.05,
            )
            st.plotly_chart(fig, width="stretch")
            st.caption("0件のバーが最も重要な情報。1件以上に絞るとこの層が見えなくなる。")

    # --- 判断4 ---
    with tabs[3]:
        ranking = metrics.booth_visit_ranking(v, booths)
        skew = metrics.booth_skew_stats(ranking)
        cc = st.columns(3)
        cc[0].metric("最多", skew["max_visits"])
        cc[1].metric("最少", skew["min_visits"])
        cc[2].metric(
            "最多/最少",
            f"{skew['ratio_max_to_min']:.1f} 倍" if skew["ratio_max_to_min"] is not None else f"差 {skew['diff_max_to_min']}",
        )
        ratio, share = skew["ratio_max_to_min"], skew["top20pct_share"]
        if ratio is None:
            verdict(st, "訪問者0のブースが存在するため倍率は定義できない。差で報告する（D-2の注記）。", "warn")
        elif ratio >= 5:
            verdict(st, "偏りは深刻。回遊の誘導に工数を割く価値がある。", "warn")
        elif ratio >= 2:
            verdict(st, "中程度。推薦アルゴリズムの副作用として緩和を狙う程度でよい。", "mid")
        else:
            verdict(st, "偏りは小さい。過剰な制御は不要。", "ok")
        if share is not None and share >= 50:
            verdict(st, f"上位20%が全訪問の {share:.1f}% を占める。倍率にかかわらず対策対象とする。", "warn")

        top_n = skew["top20pct_n_booths"]
        colors = ["firebrick" if i < top_n else "steelblue" for i in range(len(ranking))]
        fig = go.Figure(
            go.Bar(
                x=ranking["n_visits"][::-1], y=ranking["booth_short"][::-1],
                orientation="h", marker_color=colors[::-1],
            )
        )
        fig.update_layout(
            title=f"D-1 ブース別訪問者数（{day_choice}、赤 = 上位20%／占有率 {share:.1f}%）"
            if share is not None else "D-1 ブース別訪問者数",
            xaxis_title="訪問者数", height=max(500, len(ranking) * 22),
        )
        st.plotly_chart(fig, width="stretch")

        st.markdown("#### E-1 初回訪問ブース")
        firsts = metrics.first_visit_distribution(v).merge(
            booths[["booth_id", "booth_short"]], on="booth_id", how="left"
        )
        if not firsts.empty:
            total = firsts["n_first_visits"].sum()
            top3 = firsts.head(3)["n_first_visits"].sum() / total * 100
            st.markdown(f"上位3ブースの占有率: **{top3:.1f}%**")
            head = firsts.head(10)
            fig = go.Figure(
                go.Bar(x=head["n_first_visits"][::-1], y=head["booth_short"][::-1],
                       orientation="h", marker_color="darkorange")
            )
            fig.update_layout(title="E-1 初回訪問ブース上位10", xaxis_title="初回訪問者数", height=400)
            st.plotly_chart(fig, width="stretch")
            st.caption("集中度が高ければ、最初の1件を推薦で誘導する意味がある。")

    # --- 判断5 ---
    with tabs[4]:
        if day is None:
            st.warning("時間帯別の分析は日を絞って見ること。両日を重ねると時間軸が意味を失う。")
        ts = metrics.time_series(v, active)
        if ts.empty:
            st.info("該当データがありません。")
        else:
            release = metrics.recommendation_fallback_release_time(v)
            fig = go.Figure()
            fig.add_bar(x=ts["bin"], y=ts["n_new_participants"], name="新規参加者数 (B-2)", marker_color="steelblue")
            fig.add_scatter(
                x=ts["bin"], y=ts["n_concurrent"], name="同時滞在人数 (B-3)",
                yaxis="y2", line=dict(color="firebrick", width=3),
            )
            if release:
                fig.add_vline(
                    x=pd.Timestamp(release), line=dict(color="green", dash="dash"),
                    annotation_text="推薦フォールバック解除", annotation_position="top",
                )
            fig.update_layout(
                title=f"B-2 / B-3 時間帯別の人の流れ（{day_choice}）",
                xaxis_title="時刻 (JST)", yaxis=dict(title="新規参加者数"),
                yaxis2=dict(title="同時滞在人数", overlaying="y", side="right"),
                legend=dict(orientation="h", y=1.1),
            )
            st.plotly_chart(fig, width="stretch")
            st.caption("B-3 は初回チェックイン前・最終チェックイン後が不可視。新規流入のピークと滞在人数のピークのズレが判断5の対象。")

            if release:
                st.success(f"チェックイン実績者が20人に到達した時刻: **{pd.Timestamp(release):%H:%M} (JST)**。今年の推薦起動タイミングの直接の根拠。")
            else:
                st.info("チェックイン実績者が20人に到達していない（フォールバックは解除されなかった）。")

            peak_new = ts.loc[ts["n_new_participants"].idxmax(), "bin"]
            opening = ts["bin"].min()
            elapsed = (peak_new - opening).total_seconds() / 3600
            st.markdown(f"##### 新規流入のピーク: {peak_new:%H:%M}（最初のチェックインから {elapsed:.1f} 時間後）")
            if ts["n_new_participants"].std() < ts["n_new_participants"].mean() * 0.5:
                verdict(st, "流入は終日ほぼ平坦。推薦は常に不完全なデータで動く覚悟が要る。", "mid")
            elif elapsed <= 1:
                verdict(st, "開場後1時間以内に流入がピーク。「開場直後にデータを集めきる」設計が成立する。", "ok")
            else:
                verdict(st, "ピークが中盤以降。開場直後は推薦に使えるデータがない。初期はランダム提示で凌ぐ設計が必要。", "warn")

    # --- 推薦マスの効果 ---
    with tabs[5]:
        effect = metrics.recommendation_effect(p)
        rec, rnd = effect["recommended_hit_rate"], effect["random_hit_rate"]
        if rec is None or rnd is None:
            st.info("ビンゴカードのデータがありません。")
        else:
            cc = st.columns(2)
            cc[0].metric("推薦マスのチェックイン率", f"{rec * 100:.1f}%", f"{(rec - rnd) * 100:+.1f} pt")
            cc[1].metric("ランダムマスのチェックイン率", f"{rnd * 100:.1f}%")
            if rec > rnd:
                verdict(st, "去年の推薦は機能していた。同方式を踏襲してよい。", "ok")
            else:
                verdict(st, "去年の推薦は効いていない。方式そのものを見直す必要がある。", "warn")
            st.warning(
                "**この結論には限界がある。** 推薦マスは中央4マス（position 5/6/9/10）に固定配置されて"
                "いたため、位置の効果と推薦の効果が交絡している。中央マスが単に目立つから踏まれた可能性を"
                "排除できない。"
            )
            st.caption(f"推薦マス {effect['n_recommended_slots']} 枠 / ランダムマス {effect['n_random_slots']} 枠")

        bc = metrics.bingo_completion(p)
        cc = st.columns(2)
        cc[0].metric("達成マス数の中央値", f"{bc['median_n_card_hit']:.0f} / 16")
        cc[1].metric("完走率（4ライン達成）", f"{bc['pct_completed_4_lines']:.1f}%")

        vf = metrics.vote_finalization_rate(p)
        st.metric("投票確定率", f"{vf['vote_finalized_rate_pct']:.1f}%",
                  help="事後アンケートが存在しないため、今年の評価入力の回収率を見積もる代理指標として使う（F-4）")

    # --- クールタイム ---
    with tabs[6]:
        st.markdown("### 交絡1: クールタイム設定が期間中に変更された")
        st.caption("**この図を最初に見ること。** 他のすべての分析の前提になる。")
        floor = metrics.cooldown_floor(v)
        if floor.empty:
            st.info("チェックイン間隔のデータがありません。")
        else:
            sat = metrics.cooldown_saturation(v, floor)
            st.markdown(f"#### 床に張り付いた間隔の割合: **{sat * 100:.1f}%**")
            if sat >= 0.2:
                verdict(
                    st,
                    "参加者は制約いっぱいで回っていた。E-2 は仕様の天井を測っているに過ぎず、"
                    "「少なくとも X ブース/時以上」という下限としてのみ使える。"
                    "**E-3（後半にペースが落ちるか）は結論を出せない。**",
                    "warn",
                )
            else:
                verdict(st, "クールタイムは無視してよい。E-2 の中央値は実質滞在時間として読める。", "ok")

            fig = go.Figure()
            fig.add_scatter(x=floor["bin"], y=floor["min_sec"], mode="lines+markers", name="最小値")
            fig.add_scatter(x=floor["bin"], y=floor["p5_sec"], mode="lines+markers", name="5パーセンタイル")
            fig.update_layout(
                title="チェックイン間隔の床（階段状の段差が変更時刻）",
                xaxis_title="時刻 (JST)", yaxis_title="間隔（秒）", yaxis_type="log",
            )
            st.plotly_chart(fig, width="stretch")
            st.caption(
                "git履歴では 180秒 → 60秒（初日20:08のコミット）だが、運営の記憶とは食い違う。"
                "コミット時刻はデプロイ時刻ではないため、**データに刻まれた床を正とする**。"
            )

    # --- 除外候補 ---
    with tabs[7]:
        st.markdown("### 除外規則2: 運営・出展者アカウントの候補")
        st.caption("**自動除外はしない。** 誤って一般参加者を除外するリスクの方が大きいため、人が判断する。")
        open_hour = st.slider("開場時刻（この時刻より前のチェックインを手がかりとする）", 6, 12, 9)
        import build_tables

        cand = build_tables.detect_staff_candidates(v, booths, open_hour_jst=open_hour)
        if cand.empty:
            st.success("候補はありません。")
        else:
            st.dataframe(cand, width="stretch")
            st.info("除外すると決めた pid をサイドバーの「除外する pid」に貼り付けてください。")


if __name__ == "__main__":
    main()
