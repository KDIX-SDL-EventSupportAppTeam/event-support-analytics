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
import os
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth  # noqa: E402
import live_metrics as lm  # noqa: E402
import page_setup  # noqa: E402
import rec_db  # noqa: E402
import report  # noqa: E402

page_setup.configure(page_title="推薦の当日監視", page_icon="🚦")

# 既定のデータ源。Cloud Run ではイメージに焼いた合成データを指す
DEFAULT_SOURCE_DIR = os.environ.get("REC_DATA_DIR", "data/synth")

_LEVEL_COLOR = {lm.GREEN: "#16a34a", lm.YELLOW: "#d97706", lm.RED: "#dc2626", lm.UNKNOWN: "#64748b"}
_LEVEL_MARK = {lm.GREEN: "🟢", lm.YELLOW: "🟡", lm.RED: "🔴", lm.UNKNOWN: "⚪"}
REFRESH_SEC = 45
JST = "Asia/Tokyo"


def to_jst(ts):
    """UTC 保存の時刻を JST(+9) へ変換する。**画面に出す時刻は必ずこれを通す。**

    当日は暗算する余裕が無い（03 §0）。運営が見る時刻と手元の時計を一致させる。
    Series / Timestamp のどちらでも受ける。
    """
    if isinstance(ts, pd.Series):
        return pd.to_datetime(ts, utc=True).dt.tz_convert(JST)
    ts = pd.Timestamp(ts)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
    return ts.tz_convert(JST)


@st.cache_data(ttl=REFRESH_SEC)
def load_tables(source_dir: str) -> dict[str, pd.DataFrame]:
    """取得の作法（card_id の解決・イベント絞り込み・スタッフ除外）は rec_db に集約する。"""
    return rec_db.load_tables(
        rec_db.SynthSource(source_dir),
        ("card_unlock_events", "check_ins", "booth_ratings", "recommendation_scores", "bingo_cells"),
    )


def load_ops_state(source_dir: str, url: str) -> dict | None:
    """`/ops/state` の取得。**取れないことをもって画面全体を落とさない**（02 §1）。

    URL 未指定ならローカルの `ops_state.json`（リハーサル用）を読む。読めない・壊れて
    いる場合は None を返し、該当欄だけが「取得不能」になる。
    """
    if url:
        return rec_db.OpsStateClient(url).fetch()
    p = Path(source_dir) / "ops_state.json"
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


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
    except RuntimeError as exc:
        # 取得口が本番プロキシのとき、1回の失敗（500・タイムアウト）で画面を殺さない。
        # 45秒後の再描画で復帰しうるので、状況だけ出して待つ（02 §1 と同じ扱い）。
        st.error(f"{exc}\n\n{REFRESH_SEC}秒後に再試行します。")
        return
    ops = load_ops_state(source_dir, ops_url)

    st.caption(f"最終更新 {to_jst(now):%H:%M:%S} JST　/　{REFRESH_SEC}秒ごとに自動更新"
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
    st.markdown("### 当日の時系列（1枚に重ねる・横軸 JST）")
    ts = lm.time_series(t["check_ins"], t["booth_ratings"], t["card_unlock_events"])
    if not ts.empty:
        x = to_jst(ts["bin"])
        fig = go.Figure()
        fig.add_scatter(x=x, y=ts["cum_checkins"], name="累計チェックイン")
        fig.add_scatter(x=x, y=ts["cum_ratings"], name="累計評価")
        fig.add_scatter(x=x, y=ts["decision_table_size"], name="決定表件数", yaxis="y2")
        for thr in (30, 60):
            fig.add_hline(y=thr, line_dash="dot", line_color="#94a3b8")
        for _, row in lm.phase_change_times(t["card_unlock_events"]).iterrows():
            fig.add_vline(x=to_jst(row["created_at"]), line_color="#6366f1",
                          annotation_text=row["phase"])
        fig.update_layout(yaxis2=dict(overlaying="y", side="right", title="決定表件数"),
                          height=380, margin=dict(t=20, b=20), legend=dict(orientation="h"),
                          xaxis_title="時刻（JST）")
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

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
        st.caption(f"分割発動: {to_jst(prog['first_split_at']):%H:%M} JST")


def main() -> None:
    auth.require_password()  # 合言葉が未設定のローカル実行では素通りする
    st.title("🚦 推薦の当日監視")
    st.sidebar.header("データ源")
    source_dir = st.sidebar.text_input("ダンプ/合成ディレクトリ", value=DEFAULT_SOURCE_DIR)
    ops_url = st.sidebar.text_input("推薦エンジン base URL（/ops/state）", value="",
                                    help="空なら <ディレクトリ>/ops_state.json を読む。取れなくても他は動く")
    st.sidebar.caption("本番 MySQL への接続経路は未確定（E-1）。既定は合成データ。")
    render(source_dir, ops_url)


if __name__ == "__main__":
    report.guarded(main)  # 統合アプリ経由では src/app.py が同じ役割を担う
