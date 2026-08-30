"""事後の分析画面（Streamlit）。問い1つに図1つ。

仕様: docs/specs/recommendation-evaluation/04-post-analysis.md

起動:
    streamlit run src/post_analysis.py

- 当日画面（src/live_dashboard.py）とは別ファイル。重い集計をここに置く
- 指標の算出式は post_eval_metrics.py にだけ書く（二重管理しない）
- `interest_match` は凍結値を使う（再計算しない）。検出力の限界を図に注記する
- 入力は DB からのダンプ（イベント後は一度きりで足りる）＋ rules_built JSONL
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import post_eval_metrics as pem  # noqa: E402
import rec_db  # noqa: E402

st.set_page_config(page_title="推薦の事後分析", layout="wide", page_icon="📈")
LAST_YEAR_TABLES = Path("data/tables")


@st.cache_data
def load(source_dir: str) -> dict[str, pd.DataFrame]:
    src = rec_db.DumpSource(source_dir)
    names = ["check_ins", "recommendation_scores", "booth_ratings", "card_unlock_events",
             "bingo_cells", "users"]
    t = {n: src.table(n) for n in names}
    for n in ("check_ins", "recommendation_scores", "card_unlock_events"):
        t[n] = rec_db.participants_only(t[n], t["users"])
    return t


@st.cache_data
def load_last_year() -> pd.DataFrame | None:
    p = LAST_YEAR_TABLES / "participants.csv"
    return pd.read_csv(p) if p.exists() else None


@st.cache_data
def load_rules(source_dir: str) -> list[dict]:
    p = Path(source_dir) / "rules_built.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def fig1_ecdf(t: dict) -> None:
    st.subheader("① 周遊は促進されたか（Q1）— 訪問ブース数の ECDF")
    st.caption("この図はアプリ全体の効果を示す。推薦アルゴリズムの効果とは別軸（04 §2）。"
               "去年は金曜のみ・スタッフ除外の扱いは FINDINGS.md §10 を確認してから比較する。")
    out = pem.booth_count_ecdf(t["check_ins"], load_last_year())
    fig = go.Figure()
    fig.add_scatter(x=out["this_year"]["x"], y=out["this_year"]["y"], name=f"今年 (n={out['this_year']['n']})",
                    line_shape="hv")
    if "last_year_friday" in out:
        ly = out["last_year_friday"]
        fig.add_scatter(x=ly["x"], y=ly["y"], name=f"去年・金 (n={ly['n']})", line_shape="hv")
        st.write(f"中央値: 今年 **{out['this_year']['median']}** / 去年・金 **{ly['median']}**（去年 6.0 が基準）")
    fig.update_layout(xaxis_title="訪問ブース数", yaxis_title="累積割合", height=380,
                      legend=dict(orientation="h"))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def fig2_within_diff(t: dict) -> None:
    st.subheader("② DRSA は効いたか（Q2）— 参加者ごとの差のヒストグラム")
    out = pem.within_participant_diff(t["recommendation_scores"], t["check_ins"])
    if not out["diffs"]:
        st.warning("`attributes.arm` を持つ行がありません。参加者内ランダム化が未実施か、"
                   "品質ゲート通過前のデータのみです（仕様 E-2）。")
        return
    fig = go.Figure(go.Histogram(x=out["diffs"], nbinsx=21))
    fig.add_vline(x=0, line_color="#111")
    fig.add_vline(x=out["mean"], line_color="#dc2626", line_dash="dash",
                  annotation_text=f"平均 {out['mean']:+.2f}")
    fig.update_layout(xaxis_title="その人の差 = DRSA枠の訪問率 − COVERAGE枠の訪問率",
                      yaxis_title="人数", height=380)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.write(f"対象参加者数 **{out['n_participants']}**（{out['comparison']}）")
    st.info(out["caveat"])


def fig3_funnel(t: dict) -> None:
    st.subheader("③ セレンディピティ（Q3）— interest_match 別のファネル")
    high = st.slider("高評価の凍結しきい値（星4段階）", 1, 4, pem.HIGH_RATING_DEFAULT)
    f = pem.interest_match_funnel(t["recommendation_scores"], t["check_ins"], t["booth_ratings"], high)
    fig = go.Figure()
    for stage in ["presented", "visited", "rated", "high"]:
        fig.add_bar(name=stage, x=f["interest_match"], y=f[stage])
    fig.update_layout(barmode="group", height=360, yaxis_title="件数")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    show = f.copy()
    for c in ["visit_yield", "rate_yield", "high_yield"]:
        # 分母0のとき _ratio は None を返す。列が全て None だと object dtype になり
        # 乗算が TypeError になるため、数値化してから整形する
        show[c] = pd.to_numeric(show[c], errors="coerce").map(
            lambda v: "—" if pd.isna(v) else f"{v * 100:.1f}%")
    st.dataframe(show, hide_index=True)
    st.caption("MISMATCH が研究の主役。『推薦しても行かない』のか『行ったけど気に入らない』のかを分離する（04 §4）。")


def fig4_bands(t: dict) -> None:
    st.subheader("④ データ量で精度は上がったか — 決定表件数帯別の訪問率")
    st.caption("フェーズ別に色分けしない（時刻と交絡）。記述にとどめる（04 §5）。")
    out = pem.visit_rate_by_decision_table_band(
        t["recommendation_scores"], t["check_ins"], t["card_unlock_events"])
    out["band"] = out["band"].astype(str)
    fig = go.Figure(go.Bar(x=out["band"], y=out["visit_rate"], text=out["n"]))
    fig.update_layout(height=320, xaxis_title="decision_table_size の帯", yaxis_title="推薦枠への訪問率")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def fig5_rules(t: dict, rules: list[dict]) -> None:
    st.subheader("⑤ どんな規則が出たか — 規則一覧")
    if not rules:
        st.warning("`rules_built.jsonl` がありません。この表は論文にそのまま載せる想定です（04 §5）。")
        return
    st.dataframe(pem.rules_table(rules, t["recommendation_scores"]), hide_index=True)


def fig6_assigned(t: dict) -> None:
    st.subheader("⑥ 推薦されなかった候補はどうだったか — スコア分布の比較")
    out = pem.assigned_vs_unassigned_scores(t["recommendation_scores"])
    fig = go.Figure()
    fig.add_histogram(x=out["assigned"]["scores"], name=f"was_assigned=1 (n={out['assigned']['n']})", opacity=0.6)
    fig.add_histogram(x=out["unassigned"]["scores"], name=f"was_assigned=0 (n={out['unassigned']['n']})", opacity=0.6)
    fig.update_layout(barmode="overlay", height=320, xaxis_title="score")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.write(f"サニティチェック（選ばれたものは実際に上位か）: "
             f"{'✅ OK' if out['sanity_ok'] else '⚠️ 要確認'}")


def fig7_timeline(t: dict) -> None:
    st.subheader("⑦ 個票ビュー（1人の物語）")
    st.caption("仮名 ID のまま。実名・メールは扱わない。特定されうる属性の組み合わせと併用しない（04 §6）。")
    uids = sorted(t["check_ins"]["user_id"].dropna().unique())
    if not uids:
        return
    uid = st.selectbox("参加者", uids)
    tl = pem.participant_timeline(uid, t["check_ins"], t["recommendation_scores"],
                                 t["card_unlock_events"], t["booth_ratings"])
    df = pd.DataFrame(tl)
    if not df.empty:
        # 保存は UTC。読むのは JST（AGENTS.md）。仕様 04 §6 の例も JST 表記
        df["at"] = pd.to_datetime(df["at"], utc=True).dt.tz_convert("Asia/Tokyo")
    st.dataframe(df, hide_index=True, column_config={"at": st.column_config.DatetimeColumn(
        "時刻（JST）", format="HH:mm:ss")})


def main() -> None:
    st.title("📈 推薦の事後分析")
    source_dir = st.sidebar.text_input("ダンプディレクトリ", value="data/synth")
    st.sidebar.caption("イベント後のダンプ1回ぶん（CSV/Parquet）＋ rules_built.jsonl。")
    try:
        t = load(source_dir)
    except FileNotFoundError as exc:
        st.error(f"{exc}\n\n`python src/synth_rec_data.py --out {source_dir}` で合成データを作れます。")
        return
    rules = load_rules(source_dir)

    tabs = st.tabs(["① 周遊 ", "② DRSA ", "③ セレンディピティ", "④ データ量", "⑤ 規則", "⑥ 未割当", "⑦ 個票"])
    for tab, fn in zip(tabs, [fig1_ecdf, fig2_within_diff, fig3_funnel, fig4_bands,
                              lambda tt: fig5_rules(tt, rules), fig6_assigned, fig7_timeline]):
        with tab:
            fn(t)


if __name__ == "__main__":
    main()
