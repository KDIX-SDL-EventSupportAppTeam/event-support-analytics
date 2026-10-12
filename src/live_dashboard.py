"""当日の監視画面（Streamlit）。信号機。

仕様: docs/specs/recommendation-evaluation/03-live-dashboard.md

起動:
    streamlit run src/live_dashboard.py

- **事後用の重い集計と混ぜない**（別ファイル src/post_analysis.py）。混ぜると更新が止まる
- 30〜60秒で自動更新。ピーク時でもこれで十分（解放は約9.3件/分）
- データ源は DB。`REC_SOURCE=sql` で本番（読み取り専用プロキシ）、既定は合成データ。
  `/ops/state` は取れなくても止めない
- **A/B の効果（群別訪問率・その差）を表示しない**（03 §5）。実験の進捗は提示数だけ

取得口は環境変数で選ぶ（02 §4）。**既定は合成データ**（`python src/synth_rec_data.py` で生成）。
本番を読むときだけ `REC_SOURCE=sql` にし、`REC_READONLY_PROXY_URL` / `REC_READONLY_PROXY_KEY` /
`REC_EVENT_ID` を渡す。**口が無い状態で既定を本番にすると画面が落ちるので、既定は変えない。**

推薦サービスの口は `RECOMMEND_BASE_URL`（`/ops/state` と `/demo` の組み立て元）と
`RECOMMEND_OPS_TOKEN`（`X-Ops-Token`。推薦側 ADR 0008）で指定する。
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

# 取得口の選択。**既定は合成データ**（口が無い状態で画面が落ちないため）。
# 本番を読むときだけ REC_SOURCE=sql にする。接続情報は SqlSource が
# REC_READONLY_PROXY_URL / REC_READONLY_PROXY_KEY から読む（02 §4）。
SOURCE_KIND = os.environ.get("REC_SOURCE", "synth")

# 本番は過去のイベントも同じ DB に居る。**当日のイベントに絞らないと混ざる。**
# 絞り込み規則そのものは rec_db.scope_to_event にだけ書く（ADR 0001）。
EVENT_ID = os.environ.get("REC_EVENT_ID") or None

# 推薦サービスの base URL。`/ops/state` と `/demo` の両方をここから組み立てる。
# **当日 URL を手打ちさせない。** 打ち間違いは「エンジンが落ちている」と区別が付かず、
# 認証エラーや接続失敗と同じ症状に見える（03「/ops/state が取れないとき」）。
# サイドバーの入力欄は上書き用に残す。
DEFAULT_RECOMMEND_BASE_URL = (os.environ.get("RECOMMEND_BASE_URL") or "").strip()

_LEVEL_COLOR = {lm.GREEN: "#16a34a", lm.YELLOW: "#d97706", lm.RED: "#dc2626", lm.UNKNOWN: "#64748b"}
_LEVEL_MARK = {lm.GREEN: "🟢", lm.YELLOW: "🟡", lm.RED: "🔴", lm.UNKNOWN: "⚪"}
REFRESH_SEC = 45
JST = "Asia/Tokyo"


_SOURCE_HELP = {
    False: "合成データを表示中（REC_SOURCE=sql で本番の読み取り専用の口に切り替わる）。",
    True: "**本番 MySQL**（読み取り専用プロキシ）を表示中。合成データではない。",
}


def source_label(source_kind: str, source_dir: str, event_id: str | None = None) -> str:
    """**画面に「今どちらを見ているか」を必ず出す。** 取り違えると監視が意味を失う。"""
    if source_kind == "sql":
        return "本番 MySQL（読み取り専用プロキシ）" + (f" / event={event_id}" if event_id else "")
    return f"合成（{source_dir}）"


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


def make_source(source_kind: str, source_dir: str) -> rec_db.Source:
    """取得口を1箇所で選ぶ。**画面の他の場所は取得口を知らない。**

    `SqlSource` は接続情報が無ければここで RuntimeError を投げる。
    その扱いは render() 側（1回の失敗で画面を殺さない）に任せる。
    """
    if source_kind == "sql":
        return rec_db.SqlSource()
    return rec_db.SynthSource(source_dir)


@st.cache_data(ttl=REFRESH_SEC)
def load_tables(source_kind: str, source_dir: str,
                event_id: str | None = None) -> dict[str, pd.DataFrame]:
    """取得の作法（card_id の解決・イベント絞り込み・スタッフ除外）は rec_db に集約する。

    **引数は文字列だけにする。** Source を引数にすると st.cache_data が
    ハッシュできない。取得口はこの中で組み立てる。
    """
    return rec_db.load_tables(
        make_source(source_kind, source_dir),
        ("card_unlock_events", "check_ins", "booth_ratings", "recommendation_scores", "bingo_cells"),
        event_id=event_id,
    )


def load_ops_state(source_dir: str, url: str) -> rec_db.OpsStateResult:
    """`/ops/state` の取得。**取れないことをもって画面全体を落とさない**（02 §1）。

    URL 未指定ならローカルの `ops_state.json`（リハーサル用）を読む。読めない・壊れて
    いる場合は state=None を返し、該当欄だけが「取得不能」になる。

    失敗の種別（認証エラー／取得不能）も併せて返す。**当日の切り分けに要る**（03）。
    トークンは `OpsStateClient` が `RECOMMEND_OPS_TOKEN` から読む。**画面には出さない。**
    """
    if url:
        return rec_db.OpsStateClient(url).fetch_result()
    p = Path(source_dir) / "ops_state.json"
    if not p.exists():
        return rec_db.OpsStateResult(None, rec_db.OPS_UNAVAILABLE)
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return rec_db.OpsStateResult(None, rec_db.OPS_UNAVAILABLE)
    if isinstance(payload, dict):
        return rec_db.OpsStateResult(payload, rec_db.OPS_OK)
    return rec_db.OpsStateResult(None, rec_db.OPS_UNAVAILABLE)


def demo_url(base_url: str) -> str | None:
    """パラメータ調整画面 `/demo` の URL。推薦側に置いたまま**リンクで繋ぐ**（推薦側 ADR 0008 案A）。"""
    base = (base_url or "").strip().rstrip("/")
    return f"{base}/demo" if base else None


_LEVEL_WORD = {lm.GREEN: "正常", lm.YELLOW: "注意", lm.RED: "異常", lm.UNKNOWN: "不明"}

#: 信号機の各項目を「何を見ているのか」で3つに分ける。key → (やさしい名前, 何を表すか)
SIGNAL_GROUPS = (
    ("推薦エンジンは動いているか", "止まっていたら最優先で対応する。ここが赤なら研究データが取れていない",
     ("fallback_rate", "recent_unlock_count", "latency_p95")),
    ("学習データは集まっているか", "評価が集まらないと推薦は賢くならない（ランダムのまま終わる）",
     ("rating_recovery_rate", "current_phase", "ops_phase", "quality_gate")),
    ("学んだ規則の質", "DRSA が使う規則がまともか。品質ゲートで止まっていれば正常な動作",
     ("drsa_quality", "rule_coverage")),
)
SIGNAL_INFO = {
    "fallback_rate": ("推薦が失敗した割合",
                      "カードを開いたとき推薦エンジンが答えられず、予備の選び方に切り替わった割合。高い＝エンジンが落ちている"),
    "recent_unlock_count": ("直近30分にマスが開いた回数",
                            "来場者がチェックインしてマスを開けた数。極端に少ない＝アプリ側が止まっている"),
    "latency_p95": ("推薦の応答の速さ", "遅いほうから5%の応答時間。1000ms を超えるとタイムアウトして予備に切り替わる"),
    "rating_recovery_rate": ("評価してもらえた割合", "チェックインのうち★評価まで付いた割合。評価が推薦の学習材料になる"),
    "current_phase": ("記録上の段階（DB）", "最新の解放に記録された段階と、その時点の決定表の件数"),
    "ops_phase": ("エンジンが判断した段階", "推薦エンジン本体から取った段階。DB の記録と食い違ったら取り込みを疑う"),
    "quality_gate": ("DRSA へ上がる条件", "4つの条件（件数・規則数・γ・被覆率）の合否。詳しくは「推薦エンジンの中身」タブ"),
    "drsa_quality": ("規則の質（γ）", "決定表を矛盾なく説明できている割合と、確実な規則の本数"),
    "rule_coverage": ("規則が当てはまる割合", "候補のうち規則で判断できたものの割合。低い＝大半が判断保留"),
}


def _format_value(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if isinstance(value, float):
        return f"{value:.0%}" if abs(value) <= 1 else f"{value:.2f}"
    return str(value)


def signal_card(sig: lm.Signal) -> None:
    """信号機の1項目。**ライト／ダークどちらでも読める**よう、背景は状態色を薄く重ねるだけにする。"""
    color = _LEVEL_COLOR[sig.level]
    title, what = SIGNAL_INFO.get(sig.key, (sig.label, ""))
    calm = sig.level == lm.GREEN
    action = (f'<div class="sig-action sig-calm">対応不要（もし崩れたら: {sig.action}）</div>' if calm
              else f'<div class="sig-action"><b>→ こうする:</b> {sig.action}</div>')
    st.markdown(
        f"""<div class="sig-card" style="--c:{color}">
        <div class="sig-head"><span class="sig-title">{title}</span>
        <span class="sig-pill">{_LEVEL_MARK[sig.level]} {_LEVEL_WORD[sig.level]}</span></div>
        <div class="sig-what">{what}</div>
        <div class="sig-value">{_format_value(sig.value)}</div>
        <div class="sig-detail">{sig.detail}<span class="sig-tech">　指標: {sig.label}</span></div>
        {action}
        </div>""",
        unsafe_allow_html=True,
    )


def summary_banner(board: list[lm.Signal]) -> None:
    """一番上の総合判定。**何が悪いのかを名前で出す**（色だけでは当日読み取れない）。"""
    worst = lm.worst_level(board)
    bad = [s for s in board if s.level == lm.RED]
    warn = [s for s in board if s.level == lm.YELLOW]
    name = lambda s: SIGNAL_INFO.get(s.key, (s.label,))[0]  # noqa: E731
    if bad:
        head, sub = f"異常が {len(bad)} 件あります", "、".join(name(s) for s in bad)
    elif warn:
        head, sub = f"注意が {len(warn)} 件あります", "、".join(name(s) for s in warn)
    else:
        head, sub = "すべて正常です", "このまま見守ってください"
    tail = f"　（注意 {len(warn)} 件）" if bad and warn else ""
    st.markdown(
        f"""<div class="sig-banner" style="--c:{_LEVEL_COLOR[worst]}">
        <div class="sig-banner-mark">{_LEVEL_MARK[worst]}</div>
        <div><div class="sig-banner-head">{head}{tail}</div>
        <div class="sig-banner-sub">{sub}</div></div></div>""",
        unsafe_allow_html=True,
    )


@st.fragment(run_every=REFRESH_SEC)
def render(source_kind: str, source_dir: str, ops_url: str, event_id: str | None = None) -> None:
    now = pd.Timestamp.now("UTC")
    try:
        t = load_tables(source_kind, source_dir, event_id)
    except FileNotFoundError as exc:
        st.error(f"{exc}\n\n`python src/synth_rec_data.py --out {source_dir}` で合成データを作れます。")
        return
    except RuntimeError as exc:
        # 取得口が本番プロキシのとき、1回の失敗（500・タイムアウト）で画面を殺さない。
        # 45秒後の再描画で復帰しうるので、状況だけ出して待つ（02 §1 と同じ扱い）。
        st.error(f"{exc}\n\n{REFRESH_SEC}秒後に再試行します。")
        return
    ops, ops_status = load_ops_state(source_dir, ops_url)
    if ops_status == rec_db.OPS_OK:
        st.session_state["_ops_last_good"] = {"state": ops, "at": now}
    last_good = st.session_state.get("_ops_last_good")

    st.caption(f"最終更新 {to_jst(now):%H:%M:%S} JST　/　{REFRESH_SEC}秒ごとに自動更新"
               f"　/　データ源: {source_label(source_kind, source_dir, event_id)}")

    tab_signal, tab_engine, tab_people = st.tabs(["🚦 信号機", "🧠 推薦エンジンの中身", "👥 参加者とブース"])
    with tab_signal:
        _signal_tab(t, ops, ops_status, now, last_good)
    with tab_engine:
        engine_tab(t, ops, ops_status, now)
    with tab_people:
        people_tab(t, booth_names(source_kind, source_dir), now)


def _signal_tab(t, ops, ops_status, now, last_good) -> None:
    """画面1〜4（信号機・時系列・異常検知・実験の進捗）。当日の判断はこのタブだけで足りる。"""
    board = lm.signal_board(t["card_unlock_events"], t["check_ins"], t["booth_ratings"], ops, now,
                            ops_status)
    summary_banner(board)
    if ops_status == rec_db.OPS_AUTH_ERROR:
        # **エンジンの停止と混同させない。** これはこちら側の設定漏れであり、直し方が違う。
        st.error(f"`/ops/state` が認証エラー（401/403）です。`{rec_db.OpsStateClient.TOKEN_ENV}` を確認してください"
                 "（推薦サービスの `OPS_TOKEN` と同じ値）。**推薦エンジン自体は動いている可能性が高い。**"
                 "該当項目以外は動かし続けます（02 §1）。")
    elif ops_status == rec_db.OPS_TOKEN_MISSING:
        st.error(f"`{rec_db.OpsStateClient.TOKEN_ENV}` が未設定のため `/ops/state` を取得していません。"
                 "推薦サービスの `OPS_TOKEN` と同じ値を Cloud Run の env（Secret Manager 経由）に設定してください。"
                 "他の項目は動かし続けます。")
    elif ops is None:
        st.info("`/ops/state` を取得できていません（接続失敗・タイムアウト・5xx）。"
                "該当項目は「取得不能」で表示し、他は動かし続けます（02 §1）。")

    by_key = {sig.key: sig for sig in board}
    shown = set()
    for title, desc, keys in SIGNAL_GROUPS:
        sigs = [by_key[k] for k in keys if k in by_key]
        if not sigs:
            continue
        st.markdown(f'<div class="sig-group"><span>{title}</span><small>{desc}</small></div>',
                    unsafe_allow_html=True)
        cols = st.columns(min(3, len(sigs)) if len(sigs) != 4 else 2)
        for i, sig in enumerate(sigs):
            with cols[i % len(cols)]:
                signal_card(sig)
        shown |= set(keys)
    rest = [sig for sig in board if sig.key not in shown]
    if rest:
        cols = st.columns(2)
        for i, sig in enumerate(rest):
            with cols[i % 2]:
                signal_card(sig)

    if ops is None and last_good:
        st.caption(f"直近の正常値（{to_jst(last_good['at']):%H:%M:%S} JST 取得）")
        prev = lm.normalize_ops_state(last_good["state"])
        st.dataframe(pd.DataFrame([{
            "phase_current": prev.get("phase_current"),
            "decision_table_size": prev.get("decision_table_size"),
            "gamma": prev.get("gamma"),
            "n_certain_rules": prev.get("n_certain_rules"),
            "rule_coverage": prev.get("rule_coverage"),
        }]), hide_index=True)

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


@st.cache_data(ttl=600)
def booth_names(source_kind: str, source_dir: str) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """ブース名とジャンル名。**取れなくても画面は止めない**（ID の先頭8文字で代用する）。"""
    src = make_source(source_kind, source_dir)
    out = []
    for name in ("booths", "categories"):
        try:
            out.append(src.table(name))
        except Exception:  # noqa: BLE001 - 名前は飾り。取得失敗で監視を落とさない
            out.append(None)
    return out[0], out[1]


_STRATEGY_HELP = {
    "RANDOM": "ランダムに選んだマス（ランダム期の基本）",
    "DRSA": "学んだ規則で選んだマス",
    "SER": "関心外だけど刺さりそうなブースを規則で選んだマス（セレンディピティ）",
    "EXPLORE": "関心外からあえてランダムに選んだマス（偏りを防ぐ探索）",
}


def _stage_html(current: str | None) -> str:
    ids = [sid for sid, _, _ in lm.STAGES]
    cur_i = ids.index(current) if current in ids else -1
    boxes = []
    for i, (sid, label, desc) in enumerate(lm.STAGES):
        if i < cur_i:
            style, mark = "background:#e2e8f0;color:#475569;border:1px solid #cbd5e1", "✓ 済"
        elif i == cur_i:
            style, mark = "background:#4f46e5;color:#fff;border:2px solid #4338ca", "▶ いまここ"
        else:
            style, mark = "background:transparent;color:#64748b;border:1px dashed #94a3b8", "次"
        boxes.append(f'<div style="flex:1;min-width:150px;padding:10px 12px;border-radius:10px;{style}">'
                     f'<div style="font-size:.75rem;opacity:.85">{mark}</div>'
                     f'<div style="font-weight:700;font-size:1.05rem">{label}</div>'
                     f'<div style="font-size:.8rem;opacity:.9">{desc}</div></div>')
    arrow = '<div style="align-self:center;color:#94a3b8;font-size:1.2rem">→</div>'
    return f'<div style="display:flex;gap:8px;flex-wrap:wrap">{arrow.join(boxes)}</div>'


def _fmt(v, pct: bool = False) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{v:.0%}" if pct else (f"{v:.2f}" if isinstance(v, float) else str(v))


def engine_tab(t: dict[str, pd.DataFrame], ops: dict | None, ops_status: str | None,
               now: pd.Timestamp) -> None:
    """推薦エンジンの中身。運営画面では見えない「何が動いていて、次まであとどれだけか」。"""
    ov = lm.engine_overview(ops, t["booth_ratings"], now)
    if ov is None:
        st.warning("推薦エンジンの `/ops/state` が取れていないため、段階と品質ゲートは表示できません。"
                   "決定表と推薦の調子は DB から出しています。")
    else:
        st.markdown("### いま動いているアルゴリズム")
        st.markdown(_stage_html(ov["current"]), unsafe_allow_html=True)

        failing = [g for g in ov["gate_items"] if g["ok"] is False]
        if ov["current"] == "DRSA":
            st.success("**DRSA 期です。** 学んだ規則でマスを選んでいます。")
        else:
            parts = []
            if ov["remaining"]:
                eta = (f"今のペースで約 **{ov['eta_min']:.0f} 分**"
                       f"（{to_jst(now + pd.Timedelta(minutes=ov['eta_min'])):%H:%M} 頃）"
                       if ov["eta_min"] else "直近30分は評価が増えていないので見込みは出せません")
                parts.append(f"決定表があと **{ov['remaining']} 件**（{eta}）")
            for g in failing:
                if g["key"] == "size":
                    continue
                parts.append(f"{g['label']}が **{_fmt(g['value'])}**（必要 {_fmt(g['threshold'])} 以上）")
            msg = "、".join(parts) if parts else "条件はそろっています。次の再構築で切り替わります"
            st.info(f"**DRSA に上がるには:** {msg}。")

        size, need = ov["size"] or 0, ov["need"]
        st.progress(min(1.0, size / need) if need else 0.0, text=f"決定表 {size} / {need} 件")
        c1, c2, c3 = st.columns(3)
        c1.metric("評価のペース（直近30分）", f"{ov['pace_per_min'] * 60:.0f} 件/時" if ov["pace_per_min"] is not None else "—",
                  help="決定表は評価1件で1行増える。来場者に評価してもらうほど早く DRSA に上がる")
        c2.metric("確実規則（上向き / 下向き・リフト条件前）",
                  f"{_fmt(ov['rules']['certain_up'])} / {_fmt(ov['rules']['certain_down'])}",
                  help="上向き=「この条件なら高評価」、下向き=「この条件なら低評価」。"
                       "品質ゲートで数えるのは、このうち確信度が基準率より十分高い「有効な」規則だけ")
        c3.metric("高評価の基準率", _fmt(ov["rules"]["base_rate_high"], pct=True),
                  help="決定表全体で高評価が占める割合。規則はこれより確信度が高くて初めて意味がある")

        st.markdown("#### DRSA に上がる条件（品質ゲート：4つすべて満たす）")
        st.dataframe(pd.DataFrame([{
            "": "✅" if g["ok"] else ("❌" if g["ok"] is False else "？"),
            "条件": g["label"],
            "いま": _fmt(g["value"], pct=g["key"] in ("gamma", "coverage")),
            "必要": f"{_fmt(g['threshold'], pct=g['key'] in ('gamma', 'coverage'))} 以上",
            "意味": g["explain"],
        } for g in ov["gate_items"]]), hide_index=True, width="stretch")
        st.caption("しきい値は当日変えない（03 §1）。どこで止まっているかの記録が事後の見直しの根拠になる。")

        st.markdown("#### マスの選ばれ方（エンジン起動以降）")
        bs = ov["by_strategy"]
        total = sum(bs.values())
        if total:
            fig = go.Figure()
            colors = {"RANDOM": "#94a3b8", "DRSA": "#4f46e5", "SER": "#db2777", "EXPLORE": "#f59e0b"}
            for k, n in bs.items():
                fig.add_bar(y=["マス"], x=[n], name=f"{k} {n}", orientation="h", marker_color=colors.get(k),
                            hovertext=_STRATEGY_HELP.get(k, k))
            fig.update_layout(barmode="stack", height=120, margin=dict(t=10, b=10, l=10, r=10),
                              legend=dict(orientation="h"), xaxis_title=None, yaxis_visible=False)
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            st.caption(" / ".join(f"**{k}**: {v}" for k, v in _STRATEGY_HELP.items()))
        else:
            st.caption("まだ解放がありません。")

        ser = ov["ser"]
        rows, min_rows = ser["rows"] or 0, ser["min_rows"] or 0
        if ser["active"]:
            st.success(f"セレンディピティ規則（SER）: **稼働中**（{ser['count']} 本）")
        elif min_rows:
            st.progress(min(1.0, rows / min_rows),
                        text=f"セレンディピティ規則（SER）: 関心外ブースの評価 {rows} / {min_rows} 件で作り始める")
        if ser["rules"]:
            st.dataframe(pd.DataFrame(ser["rules"]), hide_index=True)

    st.divider()
    st.markdown("### 決定表（DRSA が学ぶ材料）")
    st.caption("評価1件が1行。「どんな条件の人が、そのブースをどう評価したか」。"
               "条件は推薦スコアに記録された属性で再現した近似で、エンジン内部の表と件数がずれることがある。")
    dt = lm.decision_table(t["recommendation_scores"], t["booth_ratings"], t["check_ins"])
    if dt.empty:
        st.write("まだ評価がありません。")
    else:
        grid = lm.decision_table_grid(dt)
        pm = sorted(set(grid["preference_match"]) | {0, 1, 2})
        ra = [3, 2, 1]
        z, text = [], []
        for a in ra:
            zr, tr = [], []
            for p_ in pm:
                hit = grid[(grid["preference_match"] == p_) & (grid["rating_affinity"] == a)]
                if hit.empty:
                    zr.append(None)
                    tr.append("")
                else:
                    zr.append(float(hit["high_rate"].iloc[0]))
                    tr.append(f"高評価 {hit['high_rate'].iloc[0]:.0%}<br>{int(hit['n'].iloc[0])} 件")
            z.append(zr)
            text.append(tr)
        c1, c2 = st.columns([3, 2])
        with c1:
            fig = go.Figure(go.Heatmap(
                z=z, x=[lm.PREFERENCE_LABELS.get(p_, str(p_)) for p_ in pm],
                y=[lm.AFFINITY_LABELS.get(a, str(a)) for a in ra], text=text, texttemplate="%{text}",
                colorscale=[[0, "#fee2e2"], [0.5, "#fef9c3"], [1, "#bbf7d0"]], zmin=0, zmax=1,
                showscale=False, hoverongaps=False))
            fig.update_layout(height=300, margin=dict(t=30, b=10, l=10, r=10),
                              xaxis_title="関心との一致（アンケート+行動）", yaxis_title="評価履歴との親和度",
                              title=dict(text="条件ごとの高評価率", font=dict(size=14)))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            st.caption("色の濃淡に偏りがあるほど規則が出やすい。件数が5件未満のマスは規則の根拠にならない。")
        with c2:
            counts = dt["decision"].value_counts().reindex(["高評価", "中", "低評価"]).fillna(0)
            fig = go.Figure(go.Bar(x=counts.index, y=counts.values,
                                   marker_color=["#16a34a", "#94a3b8", "#dc2626"]))
            fig.update_layout(height=300, margin=dict(t=30, b=10, l=10, r=10),
                              title=dict(text=f"評価の内訳（{len(dt)} 件）", font=dict(size=14)))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        with st.expander("決定表の直近の行"):
            booths, _ = booth_names(SOURCE_KIND, DEFAULT_SOURCE_DIR)
            show = dt.tail(20).iloc[::-1].copy()
            if booths is not None and {"id", "name"} <= set(booths.columns):
                show["booth_id"] = show["booth_id"].map(booths.set_index("id")["name"]).fillna(show["booth_id"])
            show["rated_at"] = to_jst(show["rated_at"]).dt.strftime("%H:%M")
            show["preference_match"] = show["preference_match"].map(lm.PREFERENCE_LABELS)
            show["rating_affinity"] = show["rating_affinity"].map(lm.AFFINITY_LABELS)
            st.dataframe(show.drop(columns="user_id").rename(columns={
                "booth_id": "ブース", "rating": "評価", "scale": "満点", "rated_at": "時刻",
                "preference_match": "関心との一致", "rating_affinity": "評価履歴との親和度", "decision": "判定"}),
                hide_index=True, width="stretch")

    st.divider()
    st.markdown("### 推薦の調子")
    oc = lm.recommendation_outcomes(t["recommendation_scores"], t["check_ins"], t["booth_ratings"])
    if not oc["n_assigned"]:
        st.write("まだカードに載った推薦がありません。")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("推薦が訪問された割合", _fmt(oc["visit_rate"], pct=True),
              help=f"カードに載せた {oc['n_assigned']} 件のうち、その人が実際に訪れた割合")
    c2.metric("推薦から訪れた人の高評価率", _fmt(oc["rec_high_rate"], pct=True),
              help=f"{oc['n_rec_rated']} 件の評価")
    delta = (oc["rec_high_rate"] - oc["off_high_rate"]
             if oc["rec_high_rate"] is not None and oc["off_high_rate"] is not None else None)
    c3.metric("カード外で訪れた人の高評価率", _fmt(oc["off_high_rate"], pct=True),
              delta=f"推薦のほうが {delta:+.0%}" if delta is not None else None,
              help=f"{oc['n_off_rated']} 件の評価。推薦なしで自分で選んだ訪問")
    n_eval = oc["n_rec_rated"]
    if n_eval < 20:
        st.caption(f"評価がまだ {n_eval} 件なので、良し悪しはまだ判断できません（目安 20 件〜）。")
    elif delta is not None and delta >= 0.05:
        st.success("推薦から訪れた人のほうが高評価を付けています。**好調です。**")
    elif delta is not None and delta <= -0.10:
        st.warning("推薦から訪れた人のほうが評価が低めです。決定表の偏り（上）と、関心外枠の比率を確認してください。")
    else:
        st.info("推薦とカード外で高評価率に大きな差はありません。")
    if not oc["by_match"].empty:
        st.markdown("##### 関心との一致度別の訪問率")
        bm = oc["by_match"].copy()
        bm["interest_match"] = bm["interest_match"].map(
            {"MATCH": "関心どおり", "PARTIAL": "一部一致", "MISMATCH": "関心外（セレンディピティ枠）"})
        bm["訪問率"] = bm["訪問率"].map(lambda v: f"{v:.0%}")
        st.dataframe(bm.rename(columns={"interest_match": "推薦の種類"}), hide_index=True, width="stretch")
        st.caption("関心外の推薦が訪問されていれば、このアプリの狙い（新しい出会い）が効いている。")
    st.caption("※ DRSA 枠と COVERAGE 枠の比較（A/B の効果）は当日は出しません（03 §5）。事後分析の画面で見ます。")


def people_tab(t: dict[str, pd.DataFrame], names: tuple, now: pd.Timestamp) -> None:
    """参加者の動きとブースの人気。"""
    booths, categories = names
    po = lm.participant_overview(t["check_ins"], t["booth_ratings"], now)
    c = st.columns(5)
    c[0].metric("参加者", po["n_users"], help="1回以上チェックインした人（スタッフ除く）")
    c[1].metric("いま動いている人", po["n_active"], help="直近30分にチェックインした人")
    c[2].metric("チェックイン", po["n_checkins"])
    c[3].metric("評価", po["n_ratings"])
    c[4].metric("1人あたり訪問", f"{po['visits_per_user'].mean():.1f}" if po["n_users"] else "—")

    ci = t["check_ins"]
    if ci.empty:
        st.write("まだチェックインがありません。")
        return
    c1, c2 = st.columns(2)
    with c1:
        per10 = to_jst(pd.to_datetime(ci["checked_in_at"], utc=True)).dt.floor("10min").value_counts().sort_index()
        fig = go.Figure(go.Bar(x=per10.index, y=per10.values, marker_color="#4f46e5"))
        fig.update_layout(height=280, margin=dict(t=30, b=10, l=10, r=10),
                          title=dict(text="10分ごとのチェックイン", font=dict(size=14)), xaxis_title="時刻（JST）")
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    with c2:
        vc = po["visits_per_user"].value_counts().sort_index()
        fig = go.Figure(go.Bar(x=vc.index, y=vc.values, marker_color="#0ea5e9"))
        fig.update_layout(height=280, margin=dict(t=30, b=10, l=10, r=10),
                          title=dict(text="1人あたりの訪問数（人数）", font=dict(size=14)), xaxis_title="訪問したブース数")
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    st.markdown("#### ブース別")
    pop = lm.booth_popularity(ci, t["booth_ratings"], booths, categories)
    pop["平均評価"] = pop["平均評価"].map(lambda v: f"{v:.2f}" if pd.notna(v) else "—")
    st.dataframe(pop, hide_index=True, width="stretch", height=min(600, 38 + 35 * len(pop)))


def sidebar_demo_link(base_url: str) -> None:
    """パラメータ調整画面 `/demo` へのリンク（推薦側 ADR 0008 案A）。

    **移植し直さない。** 推薦側に置いたままリンク1本で繋ぐ。ここで再実装すると
    「画面と本番の結果が違う」が起きる。注記は当日の誤解を防ぐために必ず添える。
    """
    url = demo_url(base_url)
    st.sidebar.divider()
    st.sidebar.markdown("### パラメータ調整")
    if not url:
        st.sidebar.caption("base URL を入れると推薦側の `/demo` へのリンクが出ます。")
        return
    st.sidebar.markdown(f"[推薦エンジンの `/demo` を開く]({url})")
    st.sidebar.caption(
        "**シミュレータです。** 本番設定は Cloud Run の env が正で、変更にはデプロイが必要。 "
        f"開くには `{rec_db.OpsStateClient.TOKEN_ENV}` が要ります（この画面の合言葉とは別）。"
    )


def main() -> None:
    auth.require_password()  # 合言葉が未設定のローカル実行では素通りする
    st.title("🚦 推薦の当日監視")
    st.sidebar.header("データ源")
    source_dir = st.sidebar.text_input("ダンプ/合成ディレクトリ", value=DEFAULT_SOURCE_DIR,
                                       disabled=SOURCE_KIND == "sql")
    ops_url = st.sidebar.text_input("推薦エンジン base URL（/ops/state・/demo）",
                                    value=DEFAULT_RECOMMEND_BASE_URL,
                                    help="既定は環境変数 RECOMMEND_BASE_URL。"
                                         "空なら <ディレクトリ>/ops_state.json を読む。取れなくても他は動く")
    # 当日のイベントは直前に作られるため、再デプロイせずに画面から切り替えられるようにする。
    # 空なら絞り込まない（**他のイベントと混ざる**ので本番では必ず入れる）。
    event_id = st.sidebar.text_input("イベント ID", value=EVENT_ID or "",
                                     help="既定は環境変数 REC_EVENT_ID。空だと全イベントが混ざる").strip() or None
    if SOURCE_KIND == "sql" and not event_id:
        st.sidebar.warning("イベント ID が空です。全イベントのデータが混ざって表示されます。")
    st.sidebar.caption(_SOURCE_HELP[SOURCE_KIND == "sql"])
    sidebar_demo_link(ops_url)
    render(SOURCE_KIND, source_dir, ops_url, event_id)


if __name__ == "__main__":
    report.guarded(main)  # 統合アプリ経由では src/app.py が同じ役割を担う
