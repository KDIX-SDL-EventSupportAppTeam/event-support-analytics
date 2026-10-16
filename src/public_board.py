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
- 配置は横画面・縦画面・スマホの3つを上部のボタンで切り替える（URL の ?layout= で指定もできる）。
  **縦画面とスマホは全部の情報を1画面に収める**

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
/* Streamlit の文字要素は下に -1rem の余白を持つため、そのままだと次の要素（グラフ・タイル）に重なる。打ち消す */
.pb-head, .pb-kpis, .pb-sec-head { margin-bottom:1rem !important; }
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


def _layout(fig: go.Figure, title: str, height: int, legend: bool = False, k: float = 1.0) -> go.Figure:
    """グラフ共通の見た目。k は文字の倍率（縦画面・スマホで小さくする）。"""
    fig.update_layout(height=height, margin=dict(t=round(56 * k) if title else 6, b=6, l=6, r=6),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", showlegend=legend,
                      font=dict(size=round(15 * k)),
                      title=dict(text=f"<b>{title}</b>", font=dict(size=round(22 * k)), x=0.01))
    return fig


def _buzz_chart(b: dict, height: int, k: float = 1.0) -> go.Figure:
    """10分ごとの訪問（棒）と累計（線）を1枚に重ねる。"""
    s, cum = b["per_10min"], b["cum_checkins"]
    fig = go.Figure()
    fig.add_bar(x=s.index, y=s.values, name="10分ごと", marker_color=BLUE, marker_line_width=0, opacity=.9)
    fig.add_scatter(x=cum.index, y=cum.values, name="累計", yaxis="y2", mode="lines",
                    line=dict(color=ORANGE, width=max(2, round(4 * k)), shape="hv"))
    small = k < .8
    fig.update_layout(yaxis=dict(title=None if small else "10分ごと", gridcolor=_GRID),
                      yaxis2=dict(title=None if small else "累計", overlaying="y", side="right", showgrid=False,
                                  rangemode="tozero"),
                      xaxis=dict(tickformat="%H:%M"),
                      legend=dict(orientation="h", x=1, xanchor="right", y=1.18 if small else 1.12,
                                  font=dict(size=round(15 * k))))
    return _layout(fig, f"10分ごとの訪問と累計（ピーク {b['peak']['at']:%H:%M}）", height, legend=True, k=k)


def _achieve_chart(a: dict, n_people: int, n_booths: int | None, height: int, k: float = 1.0) -> go.Figure:
    """達成の4段階を、参加者に対する割合のドーナツ4つで横に並べる。中央に人数。"""
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
                    domain=dict(x=[x0, x1], y=[.24, 1]), hoverinfo="skip")
        # xanchor を明示しないと、両端（x<1/3・x>2/3）だけ左揃え・右揃えになって中心からずれる
        fig.add_annotation(x=(x0 + x1) / 2, y=.62, xref="paper", yref="paper", showarrow=False, xanchor="center",
                           text=f"<b>{n}</b><span style='font-size:{round(18 * k)}px'>人</span>",
                           font=dict(size=round(40 * k), color=color))
        label = f"<b>{name}</b>" if k < .8 else f"<b>{name}</b><br><span style='font-size:{round(13 * k)}px'>{hint}</span>"
        fig.add_annotation(x=(x0 + x1) / 2, y=.1 if k >= .8 else .08, xref="paper", yref="paper", showarrow=False,
                           xanchor="center",
                           text=label, font=dict(size=round(16 * k)))
    return _layout(fig, "", height, k=k)


def _ratings_chart(b: dict, height: int, k: float = 1.0) -> go.Figure:
    cum = b["cum_ratings"]
    fig = go.Figure(go.Scatter(x=cum.index, y=cum.values, mode="lines", fill="tozeroy",
                               line=dict(color=PURPLE, width=max(2, round(4 * k)), shape="hv"),
                               fillcolor="rgba(124,58,237,.18)"))
    fig.update_xaxes(tickformat="%H:%M")
    fig.update_yaxes(gridcolor=_GRID, rangemode="tozero")
    return _layout(fig, "評価の累計", height, k=k)


def _visits_chart(b: dict, height: int, k: float = 1.0) -> go.Figure:
    vb = b["visit_buckets"]
    colors = [GREEN, TEAL, BLUE, PURPLE, PINK][:len(vb)]
    fig = go.Figure(go.Bar(x=[str(k_) for k_ in vb.index], y=vb.values, marker_color=colors, marker_line_width=0,
                           text=[f"{v}人" for v in vb.values], textposition="outside", cliponaxis=False,
                           textfont=dict(size=round(18 * k))))
    fig.update_xaxes(title=None if k < .8 else "回ったブース数")
    fig.update_yaxes(visible=False)
    return _layout(fig, "何ブース回った？", height, k=k)


#: 画面の向きごとの配置。**縦とスマホは1画面に全部を収める**（スクロールさせない）
LAYOUTS = {"横画面": "landscape", "縦画面": "portrait", "スマホ": "phone"}

#: 向きごとの CSS。共通の _STYLE の後に差し込み、大きさと余白だけを上書きする
_LAYOUT_CSS = {
    "landscape": "",
    "portrait": """
<style>
.block-container { padding:2.6rem 1.2rem .4rem !important; }
[data-testid="stVerticalBlock"] { gap:.55rem; }
.pb-head h1 { font-size:2.2rem !important; }
.pb-live { font-size:1.7rem; }
.pb-sec-head { margin:.9rem 0 .45rem; padding-bottom:.3rem; }
.pb-sec-head b { font-size:1.5rem; }
.pb-sec-head span { font-size:.95rem; }
/* 共通 CSS は幅 1100px 以下でタイルを2列に折り返すが、縦画面では横一列に並べて高さを節約する */
.pb-kpis { gap:.8rem; grid-template-columns:repeat(var(--n,4),1fr) !important; }
.pb-kpi { padding:.7rem 1rem .8rem 1.3rem; }
.pb-kpi-label { font-size:1.05rem; }
.pb-kpi-icon { width:1.9rem; height:1.9rem; font-size:1.05rem; }
.pb-kpi-val { font-size:3.6rem; margin:.3rem 0 .2rem; }
.pb-kpi-val small { font-size:1.3rem; }
.pb-kpi-note { font-size:.9rem; }
.pb-kpi.row .pb-kpi-val { font-size:3rem; }
.pb-foot { display:none; }
</style>
""",
    "phone": """
<style>
.block-container { padding:2.9rem .55rem .2rem !important; }
[data-testid="stVerticalBlock"] { gap:.35rem; }
/* スマホでも横並びを崩さない（Streamlit は狭い画面で列を縦に積む） */
[data-testid="stHorizontalBlock"] { flex-wrap:nowrap !important; gap:.35rem !important; }
[data-testid="stHorizontalBlock"] > div { min-width:0 !important; }
.pb-head { gap:.4rem; margin-bottom:.55rem; align-items:center; }
.pb-head h1 { font-size:1.15rem !important; }
.pb-live { font-size:.95rem; padding:.15rem .5rem; border-width:2px; gap:.35rem; }
.pb-live i { width:.6rem; height:.6rem; }
.pb-sec-head { margin:.45rem 0 .35rem; padding-bottom:.15rem; border-bottom-width:2px; }
.pb-sec-head b { font-size:.95rem; }
.pb-sec-head span { display:none; }
.pb-kpis { gap:.35rem; grid-template-columns:repeat(var(--n,4),1fr) !important; }
.pb-kpi { padding:.35rem .5rem .4rem .75rem; border-radius:10px; }
.pb-kpi::before { width:5px; }
.pb-kpi-label { font-size:.66rem; gap:.25rem; white-space:nowrap; letter-spacing:-.02em; }
.pb-kpi-icon { display:none; }
.pb-kpi-val { font-size:1.65rem; margin:.15rem 0 0; }
.pb-kpi-val small { font-size:.7rem; margin-left:.15rem; }
.pb-kpi-note { display:none; }
.pb-foot { display:none; }
</style>
""",
}


def _header(now: pd.Timestamp) -> None:
    st.markdown(f'<div class="pb-head"><h1>🎉 プロトフェス いまの会場</h1>'
                f'<span class="pb-live"><i></i><em>LIVE</em>{ld.to_jst(now):%H:%M}</span></div>', unsafe_allow_html=True)


def _venue_tiles(b: dict, n_people: int, short: bool = False) -> list[str]:
    """会場の動きの4タイル。累計（総〜）2つ → 直近30分の2つの順。ラベルは全配置で共通。"""
    m = b["active_min"]
    return [
        _kpi("総参加者数", f"{n_people:,}", "人", "ブースを1つ以上訪れた人", GREEN, icon="👥"),
        _kpi("総訪問数", f"{b['n_checkins']:,}", "回",
             f"1人平均 <b>{b['visits_mean']:.1f}</b> ブース" if b["visits_mean"] else "", BLUE, icon="📍"),
        _kpi(f"参加者数/{m}分", f"{b['n_active']:,}", "人", f"この{m}分に訪問した人", RED, icon="🚶"),
        _kpi(f"訪問数/{m}分", f"{b['recent_checkins']:,}", "回",
             _trend(b["recent_checkins"], b["prev_checkins"]), ORANGE, icon="⏱️"),
    ]


def _voice_tiles(b: dict, short: bool = False) -> list[str]:
    rpu = f"{b['ratings_per_user']:.1f}" if b["ratings_per_user"] is not None else "—"
    return [_kpi("評価" if short else "届いた評価", f"{b['n_ratings']:,}", "件", "", PURPLE, icon="📝"),
            _kpi("1人あたり" if short else "1人あたりの評価", rpu, "件", "", PINK, icon="🙋"),
            _kpi("投票" if short else "アワード投票", f"{b['n_votes']:,}", "票", "", TEAL, icon="🏆")]


def _chart(container, fig: go.Figure) -> None:
    container.plotly_chart(fig, width="stretch", config=_PLOT_CONFIG)


# 並び順は全配置で共通: 会場の動き → 評価・投票 → みんなの回り方 → ビンゴ・達成（最後に達成で締める）

def _render_landscape(b: dict, n_people: int) -> None:
    """横画面（会場のモニター）。まとまりごとに縦に並べ、スクロールして見る。"""
    _sec("会場の動き", "いま会場はどれくらい盛り上がっている？", BLUE)
    _kpis(_venue_tiles(b, n_people))
    _chart(st, _buzz_chart(b, 380))

    _sec("評価・投票", "みんなの声がどれだけ集まった？", PURPLE)
    c1, c2 = st.columns([1, 1.4], gap="large")
    with c1:
        v = _voice_tiles(b)
        _kpis(v[:2])
        _kpis([_kpi("アワード投票", f"{b['n_votes']:,}", "票", "お気に入りのブースに投票しよう", TEAL, "row", icon="🏆")])
    _chart(c2, _ratings_chart(b, 360))

    _sec("みんなの回り方", "みんなは何ブース回っている？", GREEN)
    _chart(st, _visits_chart(b, 360))

    _sec("ビンゴ・達成", "参加者のうち何人が達成した？", YELLOW)
    _chart(st, _achieve_chart(b["achievers"], n_people, b["n_booths"], 340))
    st.markdown(f'<div class="pb-foot">{ld.REFRESH_SEC}秒ごとに自動更新　/　会場全体の集計です</div>',
                unsafe_allow_html=True)


def _render_portrait(b: dict, n_people: int) -> None:
    """縦画面（縦置きのモニター 1080×1920）。**全部を1画面に収める。**"""
    k = .85
    _sec("会場の動き", "いま会場はどれくらい盛り上がっている？", BLUE)
    _kpis(_venue_tiles(b, n_people))
    _chart(st, _buzz_chart(b, 350, k))

    _sec("評価・投票", "みんなの声がどれだけ集まった？", PURPLE)
    _kpis(_voice_tiles(b))
    _chart(st, _ratings_chart(b, 200, k))

    _sec("みんなの回り方", "みんなは何ブース回っている？", GREEN)
    _chart(st, _visits_chart(b, 300, k))

    _sec("ビンゴ・達成", "参加者のうち何人が達成した？", YELLOW)
    _chart(st, _achieve_chart(b["achievers"], n_people, b["n_booths"], 270, k))


def _render_phone(b: dict, n_people: int) -> None:
    """スマホ（縦 390×844 前後）。**全部を1画面に収める。** 補足の文と凡例は省き、数字とグラフだけにする。"""
    k = .55
    _sec("会場の動き", "", BLUE)
    _kpis(_venue_tiles(b, n_people, short=True))
    _chart(st, _buzz_chart(b, 150, k))
    _sec("評価・投票", "", PURPLE)
    _kpis(_voice_tiles(b, short=True))
    _sec("みんなの回り方", "", GREEN)
    _chart(st, _visits_chart(b, 110, k))
    _sec("ビンゴ・達成", "", YELLOW)
    _chart(st, _achieve_chart(b["achievers"], n_people, b["n_booths"], 125, k))


_RENDERERS = {"landscape": _render_landscape, "portrait": _render_portrait, "phone": _render_phone}


def _trim(b: dict, start: pd.Timestamp | None, end: pd.Timestamp | None) -> dict:
    """時系列のグラフ（10分ごと・累計）だけを表示範囲で切る。前日のテストなど本番外の時刻を外すため。

    **数字のタイルは切らない。** 累計の線は範囲より前の分を含んだ値のまま、見せる区間だけを狭める。
    """
    if start is None and end is None:
        return b

    def cut(s: pd.Series) -> pd.Series:
        if s.empty:
            return s
        keep = [(start is None or t >= start) and (end is None or t <= end) for t in s.index]
        return s[keep]

    out = dict(b)
    for key in ("per_10min", "cum_checkins", "cum_ratings"):
        out[key] = cut(b[key])
    s = out["per_10min"]
    out["peak"] = {"at": s.idxmax(), "n": int(s.max())} if not s.empty else b["peak"]
    return out


@st.fragment(run_every=ld.REFRESH_SEC)
def render(source_kind: str, source_dir: str, event_id: str | None, layout: str = "landscape",
           span: tuple[pd.Timestamp | None, pd.Timestamp | None] = (None, None)) -> None:
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
    _header(now)
    if b["per_10min"].empty:
        _kpis(_venue_tiles(b, b["n_participants"], short=layout == "phone"))
        st.info("まだ訪問がありません。ブースを回ってビンゴを始めよう！")
        return
    trimmed = _trim(b, *span)
    if trimmed["per_10min"].empty:
        trimmed = b  # 範囲の指定ミスでグラフが消えるよりは、全期間を出す
    _RENDERERS[layout](trimmed, b["n_participants"])


def _layout_switch() -> str:
    """横・縦・スマホの切り替え。選んだ向きは URL の ?layout= に書き、リロードや別端末でも同じ配置で開けるようにする。"""
    by_value = {v: k for k, v in LAYOUTS.items()}
    current = by_value.get(st.query_params.get("layout", ""), "横画面")
    pick = st.segmented_control("画面の向き", list(LAYOUTS), default=current, key="pb_layout",
                                label_visibility="collapsed") or current
    layout = LAYOUTS[pick]
    if st.query_params.get("layout") != layout:
        st.query_params["layout"] = layout
    return layout


def _parse_jst(text: str) -> pd.Timestamp | None:
    try:
        return pd.Timestamp(text, tz="Asia/Tokyo")
    except (ValueError, TypeError):
        return None


def _span_picker() -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """グラフの表示範囲（JST）。URL の ?from= / ?to= に書き、リロードや別端末でも同じ範囲で開けるようにする。"""
    st.markdown("**グラフの表示範囲（JST）**")
    st.caption("前日のテストなどを外したいときに。空欄なら全期間。数字のタイルは変わりません。")
    q = st.query_params
    span = []
    for name, label, hint in (("from", "開始", "2026-10-16 10:00"), ("to", "終了", "空欄なら最新まで")):
        txt = st.text_input(label, value=q.get(name, ""), key=f"pb_{name}", placeholder=hint).strip()
        ts = _parse_jst(txt) if txt else None
        if txt and ts is None:
            st.warning(f"「{txt}」は日時として読めません。`YYYY-MM-DD HH:MM` で入れてください。")
        if ts is not None:
            if q.get(name) != txt:
                q[name] = txt
        elif name in q:
            del q[name]
        span.append(ts)
    return span[0], span[1]


def main() -> None:
    auth.require_password()  # 映す端末は運営が用意する。合言葉は運営が入れる
    st.markdown(_STYLE, unsafe_allow_html=True)
    event_id = ld.current_event_id()  # 当日監視で設定した値を共有する（URL の ?event= が優先）
    with st.sidebar:
        st.caption("来場者向け。サイドバーは « で閉じてから映してください。")
        st.caption("イベント ID は「推薦の当日監視」で設定した値を共有しています。")
        st.caption(f"データ源: {ld.source_label(ld.SOURCE_KIND, ld.DEFAULT_SOURCE_DIR, event_id)}")
        st.caption(f"イベント: {event_id or '（指定なし・全イベント）'}")
        st.divider()
        span = _span_picker()
    layout = _layout_switch()
    st.markdown(_LAYOUT_CSS[layout], unsafe_allow_html=True)
    render(ld.SOURCE_KIND, ld.DEFAULT_SOURCE_DIR, event_id, layout, span)


if __name__ == "__main__":
    report.guarded(main)
