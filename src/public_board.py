"""来場者向けボード（Streamlit）。当日、会場のモニターやプロジェクターに映す画面。

起動:
    streamlit run src/public_board.py      # 単体
    streamlit run src/app.py               # 統合アプリでは「来場者向けボード」（/live-public）

当日監視（src/live_dashboard.py）が運営・エンジニア向けなのに対し、**こちらは一般参加者が読む。**
会場全体の盛り上がりだけを見せ、「自分も回ろう」と思わせるのが目的。

- **ブースを特定できる情報を出さない**（ブース名・ブース別の数・ランキング）。出展者どうしの比較を
  来場者に見せない。ブース別の数字は運営向けの「ブース詳細」（src/booth_board.py）に分ける
- **評価の良し悪し（高評価率など）を出さない。** 件数だけ
- **個人が特定できる値を出さない。** 会場全体の集計値だけ
- **推薦の A/B に関わる数字は出さない**（03 §5）
- 指標の算出式は live_metrics.public_board にだけ書く
- イベント ID は当日監視で設定した値を共有する（live_dashboard.current_event_id）

見た目: 数字はグラフと一緒に見せる。色は**フラットな単色を複数**使い、
数字の意味ごとに色を固定する（訪問=青・参加者=緑・直近=橙・回遊中=赤・評価=紫・投票=水色）。
グラデーションや発光は使わない。数字のタイルは背景を無彩色にして数字を本文色で大きく出し、
色はアクセントの帯・アイコン・ラベルだけに使う（色付きの背景に数字を載せると見づらいため）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth  # noqa: E402
import live_dashboard as ld  # noqa: E402
import live_metrics as lm  # noqa: E402
import page_setup  # noqa: E402
import report  # noqa: E402

page_setup.configure(page_title="来場者向けボード", page_icon="🎉")

#: 意味ごとに固定するフラットな色。グラデーション・発光は使わない
BLUE, GREEN, ORANGE, RED, PURPLE, TEAL, YELLOW, PINK = (
    "#2563eb", "#16a34a", "#f97316", "#e11d48", "#7c3aed", "#0891b2", "#eab308", "#db2777")
_GENRE_COLORS = [BLUE, ORANGE, GREEN, PURPLE, RED, TEAL, YELLOW, PINK, "#64748b", "#84cc16"]
_ACHIEVE_COLORS = [YELLOW, ORANGE, RED, PURPLE]
_GRID = "rgba(128,128,128,.18)"

#: 会場のモニターで1画面に収める。サイドバーは閉じて映す前提で、本文を横幅いっぱいに使う。
#: この CSS はこの画面の要素として差し込むので、他の画面へ移ると消える
_STYLE = """
<style>
.block-container { max-width:100% !important; padding:3.2rem 1.6rem 1rem !important; }
[data-testid="stVerticalBlock"] { gap:1.2rem; }
.pb-sec-head { display:flex; align-items:baseline; gap:.9rem; margin:5.5rem 0 .4rem; padding-bottom:.5rem;
  border-bottom:3px solid var(--c); }
.pb-sec-head b { font-size:2rem; font-weight:900; }
.pb-sec-head span { font-size:1.1rem; opacity:.7; font-weight:600; }
@keyframes pb-blink { 0%,100% { opacity:1; } 50% { opacity:.3; } }

.pb-head { display:flex; align-items:center; justify-content:space-between; gap:1rem; margin:0 0 .2rem; }
.pb-head h1 { font-size:2.6rem !important; font-weight:900; margin:0; padding:0; letter-spacing:.01em; }
.pb-live { display:inline-flex; align-items:center; gap:.8rem; font-size:2.2rem; font-weight:900;
  padding:.3rem 1.1rem; border-radius:14px; border:3px solid #e11d48; font-variant-numeric:tabular-nums; }
.pb-live em { font-style:normal; color:#e11d48; letter-spacing:.06em; }
.pb-live i { width:1.2rem; height:1.2rem; border-radius:999px; background:#e11d48; animation:pb-blink 1.6s infinite; }

.pb-kpis { display:grid; grid-template-columns:repeat(var(--n,4),1fr); gap:1.4rem; }
/* 数字のタイル。**背景は無彩色、数字は本文色（最大コントラスト）。** 色はアクセントの帯・アイコン・ラベルだけに使う */
.pb-kpi { position:relative; border-radius:18px; padding:1.1rem 1.4rem 1.2rem 1.7rem; overflow:hidden;
  background:rgba(128,128,128,.10); border:1px solid rgba(128,128,128,.28); }
.pb-kpi::before { content:""; position:absolute; left:0; top:0; bottom:0; width:10px; background:var(--c); }
.pb-kpi-label { display:flex; align-items:center; gap:.55rem; font-size:1.3rem; font-weight:800; color:var(--c); }
.pb-kpi-icon { display:inline-flex; align-items:center; justify-content:center; width:2.3rem; height:2.3rem;
  border-radius:12px; font-size:1.3rem; background:color-mix(in srgb, var(--c) 18%, transparent); }
.pb-kpi-val { font-size:5.6rem; font-weight:900; line-height:1; margin:.45rem 0 .3rem; font-variant-numeric:tabular-nums; }
.pb-kpi-val small { font-size:1.9rem; font-weight:800; margin-left:.35rem; opacity:.7; }
.pb-kpi-note { font-size:1.05rem; font-weight:600; opacity:.7; }
.pb-kpi-note b { color:var(--c); opacity:1; }
.pb-kpi.row { display:flex; align-items:center; justify-content:space-between; gap:1rem; }
.pb-kpi.row .pb-kpi-val { margin:0; text-align:right; }
@media (max-width: 1100px) { .pb-kpis { grid-template-columns:repeat(2,1fr); } .pb-kpi-val { font-size:3.6rem; } }

.pb-foot { opacity:.55; font-size:.85rem; text-align:right; }
</style>
"""

_PLOT_CONFIG = {"displayModeBar": False, "staticPlot": True}


def _kpi(label: str, value: str, unit: str, note: str, color: str, variant: str = "", icon: str = "") -> str:
    """数字のタイル。variant: "row" = ラベル左・数字右の横長。"""
    head = f'<div class="pb-kpi-label"><span class="pb-kpi-icon">{icon}</span>{label}</div>' if icon \
        else f'<div class="pb-kpi-label">{label}</div>'
    if variant == "row":
        return (f'<div class="pb-kpi row" style="--c:{color}"><div>{head}<div class="pb-kpi-note">{note}</div></div>'
                f'<div class="pb-kpi-val">{value}<small>{unit}</small></div></div>')
    return (f'<div class="pb-kpi" style="--c:{color}">{head}'
            f'<div class="pb-kpi-val">{value}<small>{unit}</small></div><div class="pb-kpi-note">{note}</div></div>')


def _sec(title: str, sub: str, color: str) -> None:
    st.markdown(f'<div class="pb-sec-head" style="--c:{color}"><b>{title}</b><span>{sub}</span></div>',
                unsafe_allow_html=True)


def _kpis(tiles: list[str]) -> None:
    st.markdown(f'<div class="pb-kpis" style="--n:{len(tiles)}">{"".join(tiles)}</div>', unsafe_allow_html=True)


def _trend(recent: int, prev: int) -> str:
    diff = recent - prev
    return (f"前の30分より <b>▲ +{diff}</b>" if diff > 0
            else (f"前の30分より <b>▼ {diff}</b>" if diff < 0 else "前の30分と同じペース"))


def _layout(fig: go.Figure, title: str, height: int, legend: bool = False) -> go.Figure:
    fig.update_layout(height=height, margin=dict(t=56 if title else 10, b=8, l=8, r=8), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", showlegend=legend, font=dict(size=15),
                      title=dict(text=f"<b>{title}</b>", font=dict(size=22), x=0.01))
    return fig


def _buzz_chart(b: dict, height: int) -> go.Figure:
    """10分ごとの訪問（棒）と累計（線）を1枚に重ねる。"""
    s, cum = b["per_10min"], b["cum_checkins"]
    fig = go.Figure()
    fig.add_bar(x=s.index, y=s.values, name="10分ごと", marker_color=BLUE, marker_line_width=0, opacity=.9)
    fig.add_scatter(x=cum.index, y=cum.values, name="累計", yaxis="y2", mode="lines",
                    line=dict(color=ORANGE, width=4, shape="hv"))
    fig.update_layout(yaxis=dict(title="10分ごと", gridcolor=_GRID),
                      yaxis2=dict(title="累計", overlaying="y", side="right", showgrid=False, rangemode="tozero"),
                      xaxis=dict(tickformat="%H:%M"),
                      legend=dict(orientation="h", x=1, xanchor="right", y=1.12, font=dict(size=15)))
    return _layout(fig, f"10分ごとの訪問と累計　（ピーク {b['peak']['at']:%H:%M}）", height, legend=True)


def _achieve_chart(a: dict, n_people: int, n_booths: int | None, height: int) -> go.Figure:
    """達成の4段階を、参加者に対する割合のドーナツ4つで並べる。中央に人数。"""
    items = [("ビンゴ達成", "1ライン以上", a["bingo_1"]),
             ("ビンゴマスター", f"{a['many_lines']}ライン以上", a["bingo_many"]),
             ("コンプリート", "16マスすべて", a["card_complete"]),
             ("全ブース制覇", f"全{n_booths}ブース" if n_booths else "すべてのブース", a["all_booths"])]
    fig = go.Figure()
    for i, ((name, hint, n), color) in enumerate(zip(items, _ACHIEVE_COLORS)):
        x0, x1 = i / 4 + .01, (i + 1) / 4 - .01
        rest = max(n_people - n, 0) if n_people else 1
        fig.add_pie(values=[n, rest], hole=.68, sort=False, direction="clockwise", textinfo="none",
                    marker=dict(colors=[color, "rgba(128,128,128,.18)"], line=dict(width=0)),
                    domain=dict(x=[x0, x1], y=[.2, 1]), hoverinfo="skip")
        fig.add_annotation(x=(x0 + x1) / 2, y=.6, xref="paper", yref="paper", showarrow=False,
                           text=f"<b>{n}</b><span style='font-size:18px'>人</span>", font=dict(size=40, color=color))
        fig.add_annotation(x=(x0 + x1) / 2, y=.08, xref="paper", yref="paper", showarrow=False,
                           text=f"<b>{name}</b><br><span style='font-size:13px'>{hint}</span>", font=dict(size=16))
    return _layout(fig, "", height)


def _ratings_chart(b: dict, height: int) -> go.Figure:
    cum = b["cum_ratings"]
    fig = go.Figure(go.Scatter(x=cum.index, y=cum.values, mode="lines", fill="tozeroy",
                               line=dict(color=PURPLE, width=4, shape="hv"), fillcolor="rgba(124,58,237,.18)"))
    fig.update_xaxes(tickformat="%H:%M")
    fig.update_yaxes(gridcolor=_GRID, rangemode="tozero")
    return _layout(fig, "評価の累計", height)


def _visits_chart(b: dict, height: int) -> go.Figure:
    vb = b["visit_buckets"]
    colors = [GREEN, TEAL, BLUE, PURPLE, PINK][:len(vb)]
    fig = go.Figure(go.Bar(x=[str(k) for k in vb.index], y=vb.values, marker_color=colors, marker_line_width=0,
                           text=[f"{v}人" for v in vb.values], textposition="outside", cliponaxis=False,
                           textfont=dict(size=18)))
    fig.update_xaxes(title="回ったブース数")
    fig.update_yaxes(visible=False)
    return _layout(fig, "何ブース回った？", height)


def _genre_chart(b: dict, height: int) -> go.Figure:
    gs = b["genre_share"]
    fig = go.Figure(go.Pie(labels=gs.index, values=gs.values, hole=.55, sort=True, direction="clockwise",
                           marker=dict(colors=_GENRE_COLORS, line=dict(width=2, color="rgba(255,255,255,.6)")),
                           textinfo="percent", textfont=dict(size=15, color="#fff")))
    fig.update_layout(legend=dict(font=dict(size=14), x=1.02, y=.5))
    return _layout(fig, "どのジャンルを回った？", height, legend=True)


@st.fragment(run_every=ld.REFRESH_SEC)
def render(source_kind: str, source_dir: str, event_id: str | None) -> None:
    now = pd.Timestamp.now("UTC")
    try:
        t = ld.load_tables(source_kind, source_dir, event_id)
    except (FileNotFoundError, RuntimeError):
        # 来場者に内部のエラー文を見せない。運営は当日監視画面で原因を見る
        st.info("ただいまデータを準備中です。しばらくお待ちください。")
        return
    meta = ld.load_booth_meta(source_kind, source_dir, event_id)
    votes = lm.award_voters(meta["award_votes"], meta["users"]) if meta["award_votes"] is not None else None
    b = lm.public_board(t["check_ins"], t["booth_ratings"], t["bingo_cells"], now,
                        meta["booths"], meta["categories"], votes)
    n_people = b["n_participants"]

    st.markdown(f'<div class="pb-head"><h1>🎉 プロトフェス いまの会場</h1>'
                f'<span class="pb-live"><i></i><em>LIVE</em>{ld.to_jst(now):%H:%M}</span></div>', unsafe_allow_html=True)
    # まとまりごとに縦に並べる。1つのまとまりには「数字のタイル → グラフ1枚」までにする
    _sec("会場の動き", "いま会場はどれくらい盛り上がっている？", BLUE)
    _kpis([
        _kpi("参加者", f"{n_people:,}", "人", "ブースを1つ以上訪れた人", GREEN, icon="👥"),
        _kpi("みんなのブース訪問", f"{b['n_checkins']:,}", "回",
             f"1人平均 <b>{b['visits_mean']:.1f}</b> ブース" if b["visits_mean"] else "", BLUE, icon="📍"),
        _kpi(f"この{b['active_min']}分の訪問", f"{b['recent_checkins']:,}", "回",
             _trend(b["recent_checkins"], b["prev_checkins"]), ORANGE, icon="⏱️"),
        _kpi("いま回遊中", f"{b['n_active']:,}", "人", f"この{b['active_min']}分に訪問した人", RED, icon="🚶"),
    ])
    if b["per_10min"].empty:
        st.info("まだ訪問がありません。ブースを回ってビンゴを始めよう！")
        return
    st.plotly_chart(_buzz_chart(b, 380), width="stretch", config=_PLOT_CONFIG)

    _sec("ビンゴ・達成", "参加者のうち何人が達成した？", YELLOW)
    st.plotly_chart(_achieve_chart(b["achievers"], n_people, b["n_booths"], 340), width="stretch",
                    config=_PLOT_CONFIG)

    _sec("評価・投票", "みんなの声がどれだけ集まった？", PURPLE)
    rpu = f"{b['ratings_per_user']:.1f}" if b["ratings_per_user"] is not None else "—"
    c1, c2 = st.columns([1, 1.4], gap="large")
    with c1:
        _kpis([_kpi("届いた評価", f"{b['n_ratings']:,}", "件", "", PURPLE, icon="📝"),
               _kpi("1人あたりの評価", rpu, "件", "", PINK, icon="🙋")])
        st.write("")
        _kpis([_kpi("アワード投票", f"{b['n_votes']:,}", "票", "お気に入りのブースに投票しよう", TEAL, "row", icon="🏆")])
    c2.plotly_chart(_ratings_chart(b, 360), width="stretch", config=_PLOT_CONFIG)

    _sec("みんなの回り方", "何ブース・どのジャンルを回っている？", GREEN)
    d1, d2 = st.columns(2, gap="large")
    d1.plotly_chart(_visits_chart(b, 380), width="stretch", config=_PLOT_CONFIG)
    if not b["genre_share"].empty:
        d2.plotly_chart(_genre_chart(b, 380), width="stretch", config=_PLOT_CONFIG)

    st.markdown(f'<div class="pb-foot">{ld.REFRESH_SEC}秒ごとに自動更新　/　会場全体の集計です</div>',
                unsafe_allow_html=True)


def main() -> None:
    auth.require_password()  # 映す端末は運営が用意する。合言葉は運営が入れる
    st.markdown(_STYLE, unsafe_allow_html=True)
    event_id = ld.current_event_id()  # 当日監視で設定した値を共有する（URL の ?event= が優先）
    with st.sidebar:
        st.caption("来場者向け。サイドバーは « で閉じてから映してください。")
        st.caption("イベント ID は「推薦の当日監視」で設定した値を共有しています。")
        st.caption(f"データ源: {ld.source_label(ld.SOURCE_KIND, ld.DEFAULT_SOURCE_DIR, event_id)}")
        st.caption(f"イベント: {event_id or '（指定なし・全イベント）'}")
    render(ld.SOURCE_KIND, ld.DEFAULT_SOURCE_DIR, event_id)


if __name__ == "__main__":
    report.guarded(main)
