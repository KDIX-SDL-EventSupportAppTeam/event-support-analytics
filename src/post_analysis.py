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
import os
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth  # noqa: E402
import page_setup  # noqa: E402
import post_eval_metrics as pem  # noqa: E402
import rec_db  # noqa: E402
import report  # noqa: E402

page_setup.configure(page_title="推薦の事後分析", page_icon="📈")
LAST_YEAR_TABLES = Path("data/tables")

# 既定のデータ源。Cloud Run ではイメージに焼いた合成データを指す
DEFAULT_SOURCE_DIR = os.environ.get("REC_DATA_DIR", "data/synth")


@st.cache_data
def load(source_dir: str) -> dict[str, pd.DataFrame]:
    """取得の作法（card_id の解決・イベント絞り込み・スタッフ除外）は rec_db に集約する。"""
    return rec_db.load_tables(
        rec_db.DumpSource(source_dir),
        ("check_ins", "recommendation_scores", "booth_ratings", "card_unlock_events", "bingo_cells"),
    )


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


@st.cache_data
def load_phase_changed(source_dir: str) -> list[dict]:
    """推薦側 `phase_changed` ログ（無ければ空。DB からの復元にフォールバックする）。"""
    p = Path(source_dir) / "phase_changed.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


@st.cache_data
def load_ops_state(source_dir: str) -> dict | None:
    """`/ops/state` の凍結スナップショット（`ops_state.json`）。

    事後分析は本番プロキシを待たない（02 §4）。イベント当日に取得したものを
    ダンプと同じディレクトリに置いておく。無ければ None（`available=False` 表示になる）。
    """
    p = Path(source_dir) / "ops_state.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


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
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


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
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
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
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
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
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


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
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    st.write(f"サニティチェック（選ばれたものは実際に上位か）: "
             f"{'✅ OK' if out['sanity_ok'] else '⚠️ 要確認'}")


def fig8_engine_state(ops_state: dict | None) -> None:
    st.subheader("⑧ エンジン状態 — /ops/state の凍結値（issue #18）")
    st.caption("当日の JSONL ログ・DB からは取れず、`/ops/state` からしか取れない値。"
               "**『DRSA に上がらなかった』と『状態が取れていない』を区別する**（0/空で埋めない）。")
    s = pem.ops_state_summary(ops_state)
    if not s["available"]:
        st.warning(s["note"] + "\n\n当日取得した `ops_state.json` をダンプディレクトリに置くと表示されます。")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("現在フェーズ", s["phase_current"] or "—", help="phase.current")
    c2.metric("決定表件数", s["decision_table_size"] if s["decision_table_size"] is not None else "未提供",
              help="snapshot.decision_table_size。決定表が実際に何件まで育ったか")
    c3.metric("γ / 確実規則", f"{s['gamma']} / {s['n_certain_rules']}本")
    st.write(f"**最後の取り込み時刻（built_at）**: snapshot `{s['snapshot_built_at'] or '—'}` / "
             f"rules `{s['rules_built_at'] or '—'}`")
    st.write(f"**品質ゲート**: {s['gate_reason']}")
    gate_df = pd.DataFrame(
        [{"項目": k, "結果": "○" if v is True else "×" if v is False else "未提供(null)"}
         for k, v in s["gate_detail"].items()])
    st.dataframe(gate_df, hide_index=True)
    st.caption("gate_detail の4項目は個別に読む（issue #11 の『別の値ならどうだったか』の起点）。"
               "推薦を1件も処理していなければ null。**null を 0/false に丸めない**（02 §2）。")
    st.write(f"応答時間 p95: **{s['latency_p95_ms'] if s['latency_p95_ms'] is not None else '未提供'}** ms / "
             f"規則の被覆率: **{s['rule_coverage'] if s['rule_coverage'] is not None else '未提供'}**")


def fig9_param_validation(t: dict, ops_state: dict | None, phase_changed: list[dict]) -> None:
    st.subheader("⑨ 推薦パラメータの妥当性検証（issue #11）")
    st.caption("推薦側 ADR 0009「当日は既定値で走らせ、調整は事後」の"
               "『事後』を引き受ける。**フェーズは来場時刻と交絡する**ので群間差を効果として読まない"
               "（01 §2）。しきい値を決めるのは人間。ここは材料を出すだけ。")

    ue = t["card_unlock_events"]

    st.markdown("#### フェーズが切り替わった時刻")
    pct = pem.phase_change_times(ue, phase_changed or None)
    if pct.empty:
        st.info("切り替わりなし（COVERAGE のまま）。これも結果として記録する。")
    else:
        show = pct.copy()
        show["at"] = pd.to_datetime(show["at"], utc=True).dt.tz_convert("Asia/Tokyo")
        st.dataframe(show, hide_index=True, column_config={
            "at": st.column_config.DatetimeColumn("時刻（JST）", format="YYYY-MM-DD HH:mm")})
        st.caption(f"出所: {', '.join(sorted(pct['source'].unique()))}"
                   "（`phase_changed` ログがあれば優先、無ければ DB から復元）")

    st.markdown("#### 決定表件数の推移としきい値")
    smin = st.slider("PHASE_SIMILARITY_MIN（既定30・根拠が弱い）", 5, 120, pem.PHASE_SIMILARITY_MIN_DEFAULT, 5)
    dmin = st.slider("PHASE_DRSA_MIN（条件属性2個で60・3個で180）", 20, 240, pem.PHASE_DRSA_MIN_DEFAULT, 10)
    ts = pem.phase_comparison(ue, t["recommendation_scores"], t["check_ins"])
    ue2 = ue.copy()
    ue2["created_at"] = pd.to_datetime(ue2["created_at"], utc=True).dt.tz_convert("Asia/Tokyo")
    ue2 = ue2.sort_values("created_at")
    fig = go.Figure()
    fig.add_scatter(x=ue2["created_at"], y=ue2["decision_table_size"], mode="markers",
                    name="decision_table_size")
    fig.add_hline(y=smin, line_dash="dot", annotation_text=f"SIMILARITY_MIN={smin}")
    fig.add_hline(y=dmin, line_dash="dash", annotation_text=f"DRSA_MIN={dmin}")
    for _, row in pem.phase_change_times(ue, phase_changed or None).iterrows():
        at = pd.to_datetime(row["at"], utc=True)
        if pd.notna(at):
            fig.add_vline(x=at.tz_convert("Asia/Tokyo"), line_color="#888",
                          annotation_text=f"{row['from']}→{row['to']}")
    fig.update_layout(height=360, xaxis_title="時刻（JST）", yaxis_title="decision_table_size")
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    st.markdown("#### 実際に使われたフェーズ別の記述比較")
    st.dataframe(ts, hide_index=True)
    st.caption(ts.attrs.get("caveat", ""))

    st.markdown("#### 別のしきい値ならどのフェーズだったか（件数条件のみの再計算）")
    scenarios = dict(pem.COUNTERFACTUAL_SCENARIOS)
    scenarios[f"スライダー現在値（{smin} / {dmin}）"] = (smin, dmin)
    cf = pem.counterfactual_phase_distribution(ue, scenarios)
    st.dataframe(cf, hide_index=True)
    st.caption(f"解放 {cf.attrs.get('n_total')} 件中 {cf.attrs.get('n_measured')} 件で決定表件数が測れている。"
               "品質ゲート（規則本数・γ・被覆率）は含めていない。下の表を参照。")

    st.markdown("#### しきい値に到達したか（**未到達も結果として記録する**）")
    rep = pem.threshold_report(ue, ops_state, similarity_min=smin, drsa_min=dmin)
    st.write(rep["summary"])
    rows = []
    for c in rep["checks"]:
        mark = {True: "✅ 到達", False: "⬜ 未到達（失敗ではない）", None: "— 判定不能"}[c["reached"]]
        rows.append({"パラメータ": c["param"], "しきい値": c["threshold"],
                     "観測値": c["observed"], "判定": mark, "注記": c["note"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True)
    st.caption("規則が出ないからといってゲートを下げるのは去年の失敗の再現（推薦側 03-phases.md §3.3・R-3）。")


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
    auth.require_password()  # 合言葉が未設定のローカル実行では素通りする
    st.title("📈 推薦の事後分析")
    source_dir = st.sidebar.text_input("ダンプディレクトリ", value=DEFAULT_SOURCE_DIR)
    st.sidebar.caption("イベント後のダンプ1回ぶん（CSV/Parquet）＋ rules_built.jsonl。")
    try:
        t = load(source_dir)
    except FileNotFoundError as exc:
        st.error(f"{exc}\n\n`python src/synth_rec_data.py --out {source_dir}` で合成データを作れます。")
        return
    rules = load_rules(source_dir)
    ops_state = load_ops_state(source_dir)
    phase_changed = load_phase_changed(source_dir)

    tabs = st.tabs(["① 周遊 ", "② DRSA ", "③ セレンディピティ", "④ データ量", "⑤ 規則", "⑥ 未割当",
                    "⑦ 個票", "⑧ エンジン状態", "⑨ パラメータ妥当性"])
    for tab, fn in zip(tabs, [fig1_ecdf, fig2_within_diff, fig3_funnel, fig4_bands,
                              lambda tt: fig5_rules(tt, rules), fig6_assigned, fig7_timeline,
                              lambda _tt: fig8_engine_state(ops_state),
                              lambda tt: fig9_param_validation(tt, ops_state, phase_changed)]):
        with tab:
            fn(t)


if __name__ == "__main__":
    report.guarded(main)  # 統合アプリ経由では src/app.py が同じ役割を担う
