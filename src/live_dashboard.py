"""当日の監視画面（Streamlit）。信号機。

仕様: docs/specs/recommendation-evaluation/03-live-dashboard.md

起動:
    streamlit run src/live_dashboard.py

- **事後用の重い集計と混ぜない**（別ファイル src/post_analysis.py）。混ぜると更新が止まる
- 30〜60秒で自動更新。ピーク時でもこれで十分（解放は約9.3件/分）
- データ源は DB（ここでは DumpSource / SynthSource）。`/ops/state` は取れなくても止めない
- **A/B の効果（群別訪問率・その差）を表示しない**（03 §5）。実験の進捗は提示数だけ

接続経路が未確定のため（E-1）、既定は合成データ（`python src/synth_rec_data.py` で生成）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import live_metrics as lm  # noqa: E402
import rec_db  # noqa: E402

st.set_page_config(page_title="推薦の当日監視", layout="wide", page_icon="🚦")

_LEVEL_COLOR = {lm.GREEN: "#16a34a", lm.YELLOW: "#d97706", lm.RED: "#dc2626", lm.UNKNOWN: "#64748b"}
_LEVEL_MARK = {lm.GREEN: "🟢", lm.YELLOW: "🟡", lm.RED: "🔴", lm.UNKNOWN: "⚪"}
REFRESH_SEC = 45


@st.cache_data(ttl=REFRESH_SEC)
def load_tables(source_dir: str) -> dict[str, pd.DataFrame]:
    src = rec_db.SynthSource(source_dir)
    names = ["card_unlock_events", "check_ins", "booth_ratings", "recommendation_scores",
             "bingo_cells", "users"]
    tables = {n: src.table(n) for n in names}
    users = tables["users"]
    for key, col in [("card_unlock_events", "user_id"), ("check_ins", "user_id"),
                     ("recommendation_scores", "user_id")]:
        tables[key] = rec_db.participants_only(tables[key], users, user_col=col)
    return tables


def load_ops_state(source_dir: str, url: str) -> dict | None:
    if url:
        return rec_db.OpsStateClient(url).fetch()
    p = Path(source_dir) / "ops_state.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def signal_card(sig: lm.Signal) -> None:
    color = _LEVEL_COLOR[sig.level]
    value = sig.value
    if isinstance(value, float):
        value = f"{value:.0%}" if abs(value) <= 1 else f"{value:.2f}"
    st.markdown(
        f"""<div style="border-left:6px solid {color};background:#f8fafc;border-radius:8px;
        padding:10px 14px;margin-bottom:8px;">
        <div style="font-weight:700;font-size:0.95rem;">{_LEVEL_MARK[sig.level]} {sig.label}</div>
        <div style="font-size:1.6rem;font-weight:800;color:{color};">{value}</div>
        <div style="color:#475569;font-size:0.85rem;">{sig.detail}</div>
        <div style="margin-top:4px;font-size:0.9rem;"><b>→ こうする:</b> {sig.action}</div>
        </div>""",
        unsafe_allow_html=True,
    )


@st.fragment(run_every=REFRESH_SEC)
def render(source_dir: str, ops_url: str) -> None:
    now = pd.Timestamp.now("UTC")
    try:
        t = load_tables(source_dir)
    except FileNotFoundError as exc:
        st.error(f"{exc}\n\n`python src/synth_rec_data.py --out {source_dir}` で合成データを作れます。")
        return
    ops = load_ops_state(source_dir, ops_url)

    st.caption(f"最終更新 {pd.Timestamp.now():%H:%M:%S}　/　{REFRESH_SEC}秒ごとに自動更新"
               f"　/　データ源: 合成（{source_dir}）")

    board = lm.signal_board(t["card_unlock_events"], t["check_ins"], t["booth_ratings"], ops, now)
    worst = lm.worst_level(board)
    st.markdown(f"## {_LEVEL_MARK[worst]} 総合: {worst.upper()}")
    if ops is None:
        st.info("`/ops/state` を取得できていません。該当項目は「取得不能」で表示し、他は動かし続けます（02 §1）。")

    cols = st.columns(2)
    for i, sig in enumerate(board):
        with cols[i % 2]:
            signal_card(sig)

    st.divider()
    st.markdown("### 当日の時系列（1枚に重ねる）")
    ts = lm.time_series(t["check_ins"], t["booth_ratings"], t["card_unlock_events"])
    if not ts.empty:
        fig = go.Figure()
        fig.add_scatter(x=ts["bin"], y=ts["cum_checkins"], name="累計チェックイン")
        fig.add_scatter(x=ts["bin"], y=ts["cum_ratings"], name="累計評価")
        fig.add_scatter(x=ts["bin"], y=ts["decision_table_size"], name="決定表件数", yaxis="y2")
        for thr in (30, 60):
            fig.add_hline(y=thr, line_dash="dot", line_color="#94a3b8")
        for _, row in lm.phase_change_times(t["card_unlock_events"]).iterrows():
            fig.add_vline(x=pd.to_datetime(row["created_at"], utc=True), line_color="#6366f1",
                          annotation_text=row["phase"])
        fig.update_layout(yaxis2=dict(overlaying="y", side="right", title="決定表件数"),
                          height=380, margin=dict(t=20, b=20), legend=dict(orientation="h"))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    st.divider()
    st.markdown("### 異常検知")
    an = lm.anomalies(t["recommendation_scores"], t["bingo_cells"], t["check_ins"])
    conc = an["assignment_concentration"]
    c1, c2, c3 = st.columns(3)
    c1.metric("割当上位1ブースの占有率", f"{(conc['top1_share'] or 0):.0%}",
              help="人気順への退化の実地検出。特定ブースが大半を占めていたら何かがおかしい（03 §3）")
    c2.metric("ジニ係数（割当集中度）", f"{(conc['gini'] or 0):.2f}")
    c3.metric("カード外訪問の割合", f"{an['off_card_visit_rate']:.0%}"
              if pd.notna(an["off_card_visit_rate"]) else "—")
    st.metric("空マス（is_revealed かつ booth_id なし）", an["empty_cells"])
    if conc["top_booths"]:
        st.dataframe(pd.DataFrame(conc["top_booths"]), hide_index=True)

    st.divider()
    st.markdown("### 実験の進捗（分母のみ）")
    st.caption("A/B の効果（群別訪問率・その差）は当日表示しない。見ると設定を変えたくなり実験が壊れる（03 §5）。")
    prog = lm.experiment_progress(t["recommendation_scores"])
    if not prog["split_started"]:
        st.write("参加者内ランダム化は未発動（品質ゲート通過前）。")
    else:
        pc = st.columns(len(prog["by_arm"]) + 1)
        for i, (arm, n) in enumerate(prog["by_arm"].items()):
            pc[i].metric(f"{arm} 枠の提示数", n)
        pc[-1].metric("分割対象の参加者数", prog["n_participants"])
        st.caption(f"分割発動: {prog['first_split_at']:%H:%M}（JST 換算は +9h）")


def main() -> None:
    st.title("🚦 推薦の当日監視")
    st.sidebar.header("データ源")
    source_dir = st.sidebar.text_input("ダンプ/合成ディレクトリ", value="data/synth")
    ops_url = st.sidebar.text_input("推薦エンジン base URL（/ops/state）", value="",
                                    help="空なら <ディレクトリ>/ops_state.json を読む。取れなくても他は動く")
    st.sidebar.caption("本番 MySQL への接続経路は未確定（E-1）。既定は合成データ。")
    render(source_dir, ops_url)


if __name__ == "__main__":
    main()
