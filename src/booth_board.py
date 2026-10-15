"""ブース詳細（Streamlit）。当日の運営が「どのブースに何が起きているか」を見る画面。

起動:
    streamlit run src/booth_board.py       # 単体
    streamlit run src/app.py               # 統合アプリでは「ブース詳細」（/booths）

**来場者には見せない。** ブース名とブース別の数（ランキング・評価）を出すため、
会場に映すのは来場者向けボード（src/public_board.py）のほうにする。

当日の運営が打てる手と対にする（03 §0 の方針）:

| タブ | 運営がすること |
|---|---|
| 訪問数 / ホット | 混んでいるブースの列整理・誘導 |
| 要フォロー | 訪問が無い・途切れたブースへの声かけ、場内アナウンスで紹介 |
| 評価 | 訪問の割に評価が少ないブースで評価の声かけ（評価回収率の底上げ） |
| アワード | 結果発表の準備（上位の把握・同率の確認） |

- 算出式は live_metrics.booth_ops_table / award_ranking にだけ書く
- アワードの票は event-support-server の結果画面と同じ母集団（出展者・閲覧者の票も数える）
- 推薦の A/B に関わる数字は出さない（03 §5）
- イベント ID は当日監視で設定した値を共有する（live_dashboard.current_event_id）
"""

from __future__ import annotations

import html
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

page_setup.configure(page_title="ブース詳細", page_icon="🏷️")

_STYLE = """
<style>
.bb-award { border:1px solid rgba(128,128,128,.25); border-top:5px solid var(--c); border-radius:14px;
  padding:.8rem 1rem; margin-bottom:.8rem; background:color-mix(in srgb, var(--c) 6%, transparent); }
.bb-award h4 { margin:0; font-size:1.1rem; }
.bb-award small { opacity:.65; }
.bb-list { margin-top:.4rem; }
.bb-list.top { height:calc(var(--rows) * 3.2rem); }  /* 上位の枠は件数によらず高さを固定する */
.bb-row { display:flex; align-items:center; gap:.6rem; height:3.2rem; box-sizing:border-box;
  border-bottom:1px dashed rgba(128,128,128,.25); }
.bb-row:last-child { border-bottom:none; }
.bb-row.zero { opacity:.45; }
.bb-row.empty { justify-content:center; opacity:.4; }
.bb-rank { flex-shrink:0; width:3.6rem; white-space:nowrap; font-weight:900; font-size:1.05rem; }
.bb-name { flex:1; min-width:0; font-weight:700; line-height:1.25; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.bb-name span { display:block; font-weight:400; font-size:.78rem; opacity:.65; overflow:hidden; text-overflow:ellipsis; }
.bb-votes { font-weight:800; color:var(--c); white-space:nowrap; }
</style>
"""

_MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}
_AWARD_COLORS = ["#ec4899", "#6366f1", "#f59e0b", "#10b981", "#06b6d4", "#8b5cf6", "#f43f5e", "#84cc16"]


def _label(df: pd.DataFrame) -> pd.Series:
    """表示用のブース名。番号があれば「12 ブース名」にする（会場の掲示と照合しやすくする）。"""
    code = df["番号"].astype(str).str.strip()
    return (code + " " + df["ブース"].astype(str)).where(code != "", df["ブース"].astype(str))


def _hbar(df: pd.DataFrame, col: str, title: str, color: str, fmt: str = "{:.0f}") -> None:
    if df.empty:
        st.caption("該当するブースがありません。")
        return
    d = df.iloc[::-1]
    fig = go.Figure(go.Bar(x=d[col], y=_label(d), orientation="h", marker_color=color,
                           text=[fmt.format(v) for v in d[col]], textposition="outside"))
    fig.update_layout(height=max(220, 34 * len(d) + 60), margin=dict(t=36, b=10, l=10, r=40),
                      title=dict(text=title, font=dict(size=15)))
    fig.update_xaxes(visible=False)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def _table(df: pd.DataFrame, cols: list[str], height: int | None = None) -> None:
    show = df.assign(ブース=_label(df))[cols].copy()
    if "最終訪問" in show:
        show["最終訪問"] = show["最終訪問"].map(lambda v: f"{v:%H:%M}" if pd.notna(v) else "—")
    st.dataframe(
        show, hide_index=True, width="stretch", **({"height": height} if height else {}),
        column_config={
            "平均評価": st.column_config.ProgressColumn("平均評価", format="percent", min_value=0, max_value=1),
            "高評価率": st.column_config.NumberColumn("高評価率", format="percent"),
            "経過分": st.column_config.NumberColumn("最終訪問から（分）", format="%d"),
            "増減": st.column_config.NumberColumn("増減", format="%+d"),
        },
    )


#: アワードの枠に最初から出す行数。枠の高さはこの行数ぶんで固定する
AWARD_TOP_ROWS = 5


def _award_rows(t: pd.DataFrame) -> str:
    return "".join(
        f'<div class="bb-row{" zero" if r["票"] == 0 else ""}">'
        f'<div class="bb-rank">{_MEDAL.get(r["順位"], "") if r["票"] else ""}{r["順位"]}位</div>'
        f'<div class="bb-name">{html.escape(_name(r))}<span>{html.escape(str(r["ジャンル"]))}</span></div>'
        f'<div class="bb-votes">{r["票"]} 票</div></div>'
        for _, r in t.iterrows())


def _name(r: pd.Series) -> str:
    code = str(r.get("番号", "") or "").strip()
    return f"{code} {r['ブース']}" if code else str(r["ブース"])


def _award_card(a: dict, color: str) -> str:
    """上位 AWARD_TOP_ROWS 行だけの枠。行が足りなくても高さは変えない（並べた枠の高さを揃える）。"""
    t = a["table"]
    rows = _award_rows(t.head(AWARD_TOP_ROWS)) or '<div class="bb-row empty">ブースがありません</div>'
    return (f'<div class="bb-award" style="--c:{color}"><h4>🎖️ {html.escape(str(a["award"]))}</h4>'
            f'<small>総投票 {a["n_votes"]} 票・同数は同順位</small>'
            f'<div class="bb-list top" style="--rows:{AWARD_TOP_ROWS}">{rows}</div></div>')


def _award_detail(a: dict, color: str) -> str:
    return f'<div class="bb-list" style="--c:{color}">{_award_rows(a["table"])}</div>'


@st.fragment(run_every=ld.REFRESH_SEC)
def render(source_kind: str, source_dir: str, event_id: str | None, top_n: int, min_ratings: int) -> None:
    now = pd.Timestamp.now("UTC")
    try:
        t = ld.load_tables(source_kind, source_dir, event_id)
    except FileNotFoundError as exc:
        st.error(str(exc))
        return
    except RuntimeError as exc:
        st.error(f"{exc}\n\n{ld.REFRESH_SEC}秒後に再試行します。")
        return
    meta = ld.load_booth_meta(source_kind, source_dir, event_id)
    votes = lm.award_voters(meta["award_votes"], meta["users"]) if meta["award_votes"] is not None else None
    tb = lm.booth_ops_table(t["check_ins"], t["booth_ratings"], now, meta["booths"], meta["categories"], votes)
    hot_min, quiet_min = lm.ACTIVE_MIN, lm.QUIET_MIN

    st.caption(f"最終更新 {ld.to_jst(now):%H:%M:%S} JST　/　{ld.REFRESH_SEC}秒ごとに自動更新"
               f"　/　データ源: {ld.source_label(source_kind, source_dir, event_id)}")
    if meta["booths"] is None:
        st.warning("ブース一覧が取れませんでした。訪問の無いブースは表に出ず、名前は ID の先頭で代用しています。")

    zero = tb[tb["訪問"] == 0]
    quiet = tb[(tb["訪問"] > 0) & (tb["経過分"] >= quiet_min)]
    c = st.columns(5)
    c[0].metric("ブース数", len(tb))
    c[1].metric(f"直近{hot_min}分に訪問あり", int((tb["直近"] > 0).sum()))
    c[2].metric("まだ訪問ゼロ", len(zero), help="声かけ・場内アナウンスの候補")
    c[3].metric(f"{quiet_min}分以上訪問なし", len(quiet), help="一度は訪問があったが途切れているブース")
    c[4].metric("アワード投票", int(tb["アワード票"].sum()))

    tabs = st.tabs(["🏆 訪問数", "🔥 ホット", "⭐ 評価", "🎖️ アワード", "🙋 要フォロー", "📋 全ブース"])

    with tabs[0]:
        top = tb[tb["訪問"] > 0].head(top_n)
        c1, c2 = st.columns([3, 2])
        with c1:
            _hbar(top, "訪問", f"訪問数ランキング（上位{top_n}）", "#6366f1")
        with c2:
            genre = tb.groupby("ジャンル")["訪問"].sum().sort_values(ascending=False)
            fig = go.Figure(go.Bar(x=genre.index, y=genre.values, marker_color="#0ea5e9"))
            fig.update_layout(height=max(220, 34 * len(top) + 60), margin=dict(t=36, b=10, l=10, r=10),
                              title=dict(text="ジャンル別の訪問", font=dict(size=15)))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        _table(top, ["ブース", "ジャンル", "訪問", "直近", "評価数", "アワード票", "最終訪問"])

    with tabs[1]:
        st.caption(f"直近{hot_min}分の訪問。「増減」はその前の{hot_min}分との差。混雑しているブースの列整理・誘導に。")
        hot = tb[tb["直近"] > 0].sort_values(["直近", "増減"], ascending=False).head(top_n)
        rising = tb[tb["増減"] > 0].sort_values(["増減", "直近"], ascending=False).head(top_n)
        c1, c2 = st.columns(2)
        with c1:
            _hbar(hot, "直近", f"🔥 いま熱い（直近{hot_min}分）", "#f43f5e")
        with c2:
            _hbar(rising, "増減", "📈 急上昇（前の30分からの増加）", "#f59e0b", "+{:.0f}")
        _table(hot, ["ブース", "ジャンル", "直近", "その前", "増減", "訪問", "最終訪問"])

    with tabs[2]:
        st.caption(f"評価が {min_ratings} 件以上のブースだけを並べる（少数の満点で上位に出るのを防ぐ）。"
                   "件数はサイドバーで変えられる。")
        rated = tb[tb["評価数"] >= min_ratings].sort_values(["平均評価", "評価数"], ascending=False)
        c1, c2 = st.columns(2)
        with c1:
            _hbar(rated.head(top_n), "平均評価", f"⭐ 平均評価が高い（上位{top_n}）", "#10b981", "{:.0%}")
        with c2:
            _hbar(tb.sort_values("評価数", ascending=False).head(top_n), "評価数",
                  f"📝 評価が多い（上位{top_n}）", "#8b5cf6")
        _table(rated.head(max(top_n, 10)), ["ブース", "ジャンル", "平均評価", "高評価率", "評価数", "訪問"])

    with tabs[3]:
        ranks = lm.award_ranking(votes if votes is not None else pd.DataFrame(columns=["award_id", "booth_id"]),
                                 meta["awards"], meta["booths"], meta["categories"])
        if not ranks:
            st.info("このイベントにはアワードがありません（または取得できませんでした）。")
        else:
            st.caption(f"各賞の上位{AWARD_TOP_ROWS}ブース。「詳細」で票0のブースまで全部の順位を出す。"
                       "**同数は同順位**（event-support-server の結果画面と同じ規則・同じ母集団）。")
            cols = st.columns(min(3, len(ranks)))
            for i, a in enumerate(ranks):
                color = _AWARD_COLORS[i % len(_AWARD_COLORS)]
                with cols[i % len(cols)]:
                    st.markdown(_award_card(a, color), unsafe_allow_html=True)
                    if len(a["table"]) > AWARD_TOP_ROWS:
                        with st.expander(f"詳細（全{len(a['table'])}ブース）"):
                            st.markdown(_award_detail(a, color), unsafe_allow_html=True)

    with tabs[4]:
        st.markdown(f"##### 🫥 まだ訪問ゼロ（{len(zero)}）")
        st.caption("場内アナウンスでの紹介や、近くの来場者への声かけの候補。")
        if zero.empty:
            st.success("全ブースに訪問があります。")
        else:
            _table(zero, ["ブース", "ジャンル"])
        st.markdown(f"##### ⏳ {quiet_min}分以上訪問なし（{len(quiet)}）")
        if quiet.empty:
            st.success(f"{quiet_min}分以上訪問が途切れているブースはありません。")
        else:
            _table(quiet.sort_values("経過分", ascending=False), ["ブース", "ジャンル", "経過分", "最終訪問", "訪問"])
        st.markdown("##### 📝 訪問の割に評価が少ない")
        st.caption("訪問が5回以上で、評価が訪問の半分未満。ブースで評価の声かけを（評価回収率の底上げ）。")
        low = tb[(tb["訪問"] >= 5) & (tb["評価数"] < tb["訪問"] / 2)].assign(
            回収率=lambda d: d["評価数"] / d["訪問"]).sort_values("回収率")
        if low.empty:
            st.success("該当なし。")
        else:
            show = low.assign(ブース=_label(low))[["ブース", "ジャンル", "訪問", "評価数", "回収率"]]
            st.dataframe(show, hide_index=True, width="stretch",
                         column_config={"回収率": st.column_config.NumberColumn(format="percent")})

    with tabs[5]:
        genres = sorted(tb["ジャンル"].unique())
        pick = st.multiselect("ジャンルで絞る", genres, key="bb_genre")
        q = st.text_input("ブース名・番号で検索", key="bb_q").strip()
        view = tb[tb["ジャンル"].isin(pick)] if pick else tb
        if q:
            view = view[_label(view).str.contains(q, case=False, regex=False)]
        _table(view, ["ブース", "ジャンル", "訪問", "直近", "増減", "評価数", "平均評価", "高評価率",
                      "アワード票", "最終訪問", "経過分"], height=min(700, 38 + 35 * max(len(view), 1)))
        csv = view.assign(ブース=_label(view)).drop(columns=["番号"]).to_csv(index=False).encode("utf-8-sig")
        st.download_button("CSV でダウンロード", csv, file_name="booths.csv", mime="text/csv")


def main() -> None:
    auth.require_password()  # 合言葉が未設定のローカル実行では素通りする
    st.markdown(_STYLE, unsafe_allow_html=True)
    st.title("🏷️ ブース詳細")
    st.caption("運営用。**ブース名と順位が出るので会場には映さない**（映すのは「来場者向けボード」）。")
    event_id = ld.current_event_id()  # 当日監視で設定した値を共有する（URL の ?event= が優先）
    with st.sidebar:
        st.caption("イベント ID は「推薦の当日監視」で設定した値を共有しています。")
        st.caption(f"イベント: {event_id or '（指定なし・全イベント）'}")
        top_n = st.slider("ランキングの件数", 5, 30, 10, key="bb_top_n")
        min_ratings = st.slider("評価ランキングに載せる最低評価数", 1, 10, 3, key="bb_min_ratings")
    render(ld.SOURCE_KIND, ld.DEFAULT_SOURCE_DIR, event_id, top_n, min_ratings)


if __name__ == "__main__":
    report.guarded(main)
