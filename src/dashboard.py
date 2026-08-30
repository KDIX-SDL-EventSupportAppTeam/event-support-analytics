"""ローカルGUIダッシュボード（Streamlit）。

起動:
    streamlit run src/dashboard.py

data/tables/ を読むだけで、個人データを含むファイルを新たに生成しない。
指標の定義は metrics.py を経由し、ここでは再実装しない（仕様の二重管理を避ける）。

判断基準は docs/.sdd/01-context/decision-criteria.md で事前に固定されている。
本ダッシュボードは絞り込みの結果を、そのしきい値に自動で当てはめて表示する。

画面設計の方針:
    非エンジニアが単独で読めることを優先する。各タブは「何がわかるか →
    結論 → 図 → 図の読み方 → 専門的な注意点」の順で構成し、
    専門的な内容と上級者向けの操作は既定で折りたたむ。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth  # noqa: E402
import metrics  # noqa: E402
import page_setup  # noqa: E402
import report  # noqa: E402

TABLES_DIR = Path("data/tables")
MIN_CELL_SIZE = 3  # 該当者がこれ未満の絞り込みは集計を表示しない（privacy-policy.md）

PLOTLY_CONFIG = {"displayModeBar": False, "scrollZoom": False}

page_setup.configure(page_title="プロトフェス行動データ", page_icon="📊")


# --- 見た目 -------------------------------------------------------------------

STYLE = """
<style>
/* 全体の余白と文字サイズ（画面を読み物として成立させる） */
.block-container { padding-top: 2.2rem; max-width: 1300px; }

/* 操作できる要素はすべて「押せそう」に見せる */
.stButton > button,
.stDownloadButton > button {
    background: #1a56db; color: #fff; border: 0; border-radius: 8px;
    padding: 0.55rem 1.2rem; font-weight: 700;
    box-shadow: 0 2px 6px rgba(26, 86, 219, .35);
}
.stButton > button:hover, .stDownloadButton > button:hover {
    background: #1e429f; color: #fff; box-shadow: 0 4px 10px rgba(26, 86, 219, .45);
}
[data-testid="stSidebar"] [role="radiogroup"] label,
[data-testid="stSidebar"] [data-testid="stCheckbox"] label {
    cursor: pointer; border-radius: 8px; padding: 2px 6px;
}
[data-testid="stSidebar"] [role="radiogroup"] label:hover,
[data-testid="stSidebar"] [data-testid="stCheckbox"] label:hover {
    background: rgba(26, 86, 219, .10);
}

/* タブ = 主要ナビゲーション。押せることが一目でわかる大きさにする */
.stTabs [data-baseweb="tab-list"] { gap: 6px; flex-wrap: wrap; }
.stTabs [data-baseweb="tab"] {
    background: #eef2ff; border-radius: 10px 10px 0 0;
    padding: 10px 16px; font-weight: 700; font-size: 15px; cursor: pointer;
}
.stTabs [data-baseweb="tab"]:hover { background: #dbe4ff; }
.stTabs [aria-selected="true"] { background: #1a56db !important; color: #fff !important; }

/* 数値カード */
[data-testid="stMetric"] {
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px;
    padding: 12px 16px;
}
[data-testid="stMetricValue"] { font-size: 1.9rem; font-weight: 800; }
[data-testid="stMetricLabel"] p { font-size: .92rem; color: #475569; }

/* 折りたたみ（詳しい話はここに隠す） */
[data-testid="stExpander"] details {
    border: 1px solid #e2e8f0; border-radius: 10px; background: #fcfcfd;
}
[data-testid="stExpander"] summary { font-weight: 700; cursor: pointer; }
[data-testid="stExpander"] summary:hover { color: #1a56db; }

/* 説明ボックス */
.lead {
    background: #eff6ff; border-left: 6px solid #1a56db; border-radius: 8px;
    padding: 14px 18px; margin: 4px 0 18px 0; line-height: 1.75;
}
.lead .q { font-weight: 800; font-size: 1.05rem; display: block; margin-bottom: 4px; }
.howto {
    background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 8px;
    padding: 10px 14px; margin: 4px 0 22px 0; font-size: .93rem; line-height: 1.7;
}
.hint { color: #64748b; font-size: .88rem; }
</style>
"""


def lead(question: str, body: str) -> None:
    """タブの冒頭に「この画面で何がわかるか」を平易な言葉で置く。"""
    st.markdown(f'<div class="lead"><span class="q">{question}</span>{body}</div>', unsafe_allow_html=True)


def howto(text: str) -> None:
    """図のすぐ下に置く、読み方のガイド。"""
    st.markdown(f'<div class="howto">🔍 <b>グラフの見方</b>　{text}</div>', unsafe_allow_html=True)


def note(title: str, body: str) -> None:
    """専門的な但し書き。既定で閉じておき、必要な人だけが開く。"""
    with st.expander(f"⚠️ {title}"):
        st.markdown(body)


def styled(fig: go.Figure, title: str, subtitle: str = "") -> go.Figure:
    """図のタイトル・凡例・ホバーの体裁を統一する。"""
    head = f"<b>{title}</b>"
    if subtitle:
        head += f"<br><span style='font-size:13px;color:#64748b'>{subtitle}</span>"
    # タイトル（2行）と凡例が描画領域に重ならないよう、上の余白を広めに確保する。
    # タイトルは container 基準に固定し、凡例をその下・プロットの上に置く。
    fig.update_layout(
        title=dict(text=head, x=0, xanchor="left", font=dict(size=19), yref="container", y=0.97, yanchor="top"),
        margin=dict(t=150, l=60, r=40, b=60),
        hoverlabel=dict(font_size=15, bgcolor="white"),
        plot_bgcolor="#ffffff",
        font=dict(size=14),
        height=560,  # 上の余白を広げたぶん、描画領域が潰れないよう全体を高くする
        legend=dict(orientation="h", y=1.06, yanchor="bottom", x=0),
    )
    fig.update_xaxes(gridcolor="#eef2f6", zerolinecolor="#e2e8f0", title_font_size=14)
    fig.update_yaxes(gridcolor="#eef2f6", zerolinecolor="#e2e8f0", title_font_size=14)
    return fig


def show(fig: go.Figure) -> None:
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)


# --- データ読み込み -----------------------------------------------------------


@st.cache_data
def load(tables_dir: str):
    visits, participants, booths = metrics.load_tables(Path(tables_dir))
    return visits, participants, booths


def multiselect_all(label: str, series: pd.Series, help: str | None = None):
    """欠損を除いたユニーク値の複数選択。既定は全選択。"""
    options = sorted(str(v) for v in series.dropna().unique())
    return st.sidebar.multiselect(label, options, default=options, help=help)


def verdict(box, text: str, level: str) -> None:
    icon = {"warn": "🔴 **要注意**", "mid": "🟡 **どちらとも言える**", "ok": "🟢 **問題なし**"}[level]
    {"warn": box.error, "mid": box.warning, "ok": box.success}[level](f"{icon}\n\n{text}")


# --- 本体 ---------------------------------------------------------------------


def main() -> None:
    st.markdown(STYLE, unsafe_allow_html=True)
    auth.require_password()  # 合言葉が未設定のローカル実行では素通りする
    st.title("📊 第3回プロトフェス 来場者行動データ")
    st.markdown(
        '<div class="lead"><span class="q">この画面は何のためのもの？</span>'
        "2025年のプロトフェス（去年）で、来場者が実際にどう会場を回ったかの記録です。"
        "2026年のアプリを「どう作るか」を決めるための材料になります。<br>"
        "上のタブが5つの問いに対応しています。<b>左側で条件をしぼる → タブを選んで結論を読む</b>、"
        "という順に使ってください。数字の意味がわからないときは、各タブの"
        "「グラフの見方」を読めば足ります。</div>",
        unsafe_allow_html=True,
    )

    with st.expander("📘 はじめての人へ（用語と使い方）"):
        st.markdown(
            "| 言葉 | 意味 |\n|---|---|\n"
            "| チェックイン | 来場者がブースでアプリのボタンを押した記録。1ブースにつき1回だけ |\n"
            "| 滞在時間 | 最初のチェックインから最後のチェックインまでの時間。"
            "受付〜最初、最後〜退場は記録に残らないので、**実際はこれより長い** |\n"
            "| 単発訪問者 | チェックインが1回だけの人。滞在時間が計算できない |\n"
            "| 延べ人数 | 「人 × 日」で数えた数。2日とも来た人は2人ぶんと数える |\n"
            "| 中央値 | 小さい順に並べたときのちょうど真ん中の値。"
            "極端な人に引っ張られないので、平均より実感に近い |\n"
            "| 推薦マス | ビンゴカードのうち、アプリがその人向けに選んだマス |\n\n"
            "**操作のしかた**：左の「絞り込み」で日付や年代を選ぶと、全タブの数字が同時に変わります。"
            "グラフは**マウスを乗せる（ホバーする）と、その棒や点の中身が数字で出ます**。"
            "ドラッグすれば範囲を拡大でき、ダブルクリックで元に戻ります。"
        )

    if not (TABLES_DIR / "participants.csv").exists():
        report.show_data_missing_screen(
            f"`{TABLES_DIR}` に中間テーブルがありません。",
            advice="担当者向け: `python src/dump_firestore.py` と `python src/run_pipeline.py` を"
            "実行してからデプロイし直してください。",
        )

    visits, participants, booths = load(str(TABLES_DIR))

    # === サイドバー: 絞り込み ===
    st.sidebar.header("🔎 絞り込み")
    st.sidebar.caption("ここを変えると、右側のすべての数字とグラフが連動して変わります。")

    day_choice = st.sidebar.radio(
        "① 見たい開催日",
        ["両日", "金曜 (10/10)", "土曜 (10/11)"],
        help=(
            "迷ったら「金曜 (10/10)」を選んでください。今年は金曜1日開催のため、"
            "金曜のデータが今年の見積もりに最も近くなります。\n\n"
            "（技術的な注記: チェックイン間隔に依存する指標 E-2/E-3 は、"
            "クールタイム値が一定な土曜を主とする）"
        ),
    )
    day = {"両日": None, "金曜 (10/10)": metrics.FRIDAY, "土曜 (10/11)": metrics.SATURDAY}[day_choice]

    ages = multiselect_all("② 年代", participants["age"], help="全部選んだ状態が「全員」です。")
    genders = multiselect_all("③ 性別", participants["gender"])
    genres = multiselect_all("④ 興味ジャンル", participants["genre"])
    st.sidebar.caption("②〜④は既定で全選択（＝全員）です。選択を外すと、その層だけを見られます。")

    st.sidebar.divider()
    with st.sidebar.expander("⚙️ 詳しい設定（ふだんは触らなくてよい）"):
        include_zero = st.checkbox(
            "チェックイン0件の参加者を含める",
            value=True,
            help="「登録したがアプリを使わなかった層」。判断3に直結するため既定で含める",
        )
        include_single = st.checkbox(
            "単発訪問者（1ブースのみ）を含める",
            value=True,
            help="1回しかチェックインしていない人。滞在時間は計算できない",
        )
        exclude_raw = st.text_area(
            "除外する pid（運営・出展者。1行1件）",
            value="",
            help="除外規則2は自動判定しない。「おまけ: 除外候補」タブで確認してから指定する",
        )
    exclude_pids = {p.strip() for p in exclude_raw.splitlines() if p.strip()}

    st.sidebar.divider()
    if st.sidebar.button("🔄 表示を更新する", width="stretch"):
        st.rerun()

    # 画面は出ているが様子がおかしい、という場合の出口。
    # 例外で止まったときは report.guarded が同じ内容を自動で出す。
    with st.sidebar.expander("❓ 表示がおかしいときは"):
        st.markdown(
            "まずブラウザを再読み込みしてください"
            "（Windows: `Ctrl` + `R` ／ Mac: `⌘` + `R`）。\n\n"
            "直らないときは、下の内容をコピーして"
            f"{report.SUPPORT_CONTACT}に送ってください。"
        )
        st.code(report.build_report("画面の表示がおかしい（利用者からの申告）"), language="text")

    # 不具合レポートに載せる「そのとき何を見ていたか」
    report.set_context(
        開催日=day_choice,
        年代=", ".join(ages) or "（選択なし）",
        性別=", ".join(genders) or "（選択なし）",
        興味ジャンル=", ".join(genres) or "（選択なし）",
        チェックイン0件を含める=include_zero,
        単発訪問者を含める=include_single,
        除外pid件数=len(exclude_pids),
    )

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
    st.subheader(f"いま見ている範囲：{day_choice}")
    c = st.columns(5)
    c[0].metric("参加者（延べ）", f"{len(active)}", help="「人 × 日」で数えた数。2日とも来た人は2と数える")
    c[1].metric("実人数", f"{n_people}", help="同じ人を1人として数えた数")
    c[2].metric("チェックイン", f"{int(active['n_booths'].sum())}", help="ブースで押された回数の合計")
    c[3].metric(
        "滞在時間の中央値",
        f"{dwell['median']:.0f} 分" if dwell["median"] is not None else "—",
        help="ちょうど真ん中の人の滞在時間。単発訪問者は計算できないため除外（交絡3）",
    )
    c[4].metric(
        "訪問ブース数の中央値",
        f"{counts['median_n_booths']:.1f}" if counts["median_n_booths"] is not None else "—",
        help="ちょうど真ん中の人が回ったブースの数",
    )
    st.markdown(
        '<div class="hint">💡 カード名の横の「?」にマウスを乗せると、その数字の意味が出ます。</div>',
        unsafe_allow_html=True,
    )
    note(
        "滞在時間の数字を読む前に（1分で読めます）",
        "滞在時間は「最終チェックイン − 初回チェックイン」による代用値であり、"
        "**真の滞在時間の下限**です（受付〜初回、最終〜退場のラグが失われるため）。"
        "「実際にはこれより長い」と考えて読んでください。",
    )

    tabs = st.tabs(
        [
            "① 来た人はどんな人？",
            "② 今年は何件集まる？",
            "③ 推薦は誰に届く？",
            "④ ブースの人気の偏り",
            "⑤ 人が集まる時間帯",
            "⑥ 推薦は効いていた？",
            "前提の確認",
            "おまけ: 除外候補",
        ]
    )

    # --- 判断1 ---
    with tabs[0]:
        lead(
            "問い1: 来場者の典型は「さっと数ブース」か「じっくり多ブース」か？",
            "これがわかると、推薦機能を<b>誰に向けて作るか</b>が決まります。"
            "短時間の人が多数派なら、推薦を凝るより「短い滞在でも楽しめる設計」が先になります。",
        )
        pct = dwell["pct_under_30min"]
        if pct is None:
            st.info("この条件に当てはまるデータがありません。左の絞り込みを緩めてください。")
        else:
            st.markdown(f"### 滞在30分未満の人の割合: **{pct:.1f}%**")
            if pct >= 50:
                verdict(st, "短時間層が多数派。推薦アルゴリズムの精緻化より「短時間滞在者をどう扱うか」が主論点。", "warn")
            elif pct >= 20:
                verdict(st, "二層構造。短時間層と長時間層で別々の体験設計が要る。", "mid")
            else:
                verdict(st, "長時間滞在が標準。推薦の作り込みに投資する価値がある。", "ok")

            multi = active[active["is_single"] == False]  # noqa: E712
            d = multi["dwell_min"].dropna().clip(upper=180)
            fig = go.Figure(
                go.Histogram(
                    x=d,
                    xbins=dict(start=0, end=180, size=30),
                    marker_color="#1a56db",
                    hovertemplate="滞在時間 %{x} 分の人<br><b>%{y} 人</b><extra></extra>",
                )
            )
            styled(
                fig,
                "滞在した時間の分布",
                f"横=会場にいた時間 ／ 縦=その時間だった人数（{day_choice}・"
                f"単発訪問者 {dwell['n_single']} 名を除外・いちばん右は3時間以上）",
            )
            fig.update_layout(xaxis_title="滞在時間（分）", yaxis_title="人数", bargap=0.05)
            show(fig)
            howto(
                "山が左に寄っていれば「さっと帰る人」が多く、右に広がっていれば「長く滞在する人」が多い、"
                "ということです。<b>棒にマウスを乗せると、その時間帯だった人数が表示されます。</b>"
            )

            if dwell["median"] is not None:
                cc = st.columns(4)
                cc[0].metric("真ん中の人", f"{dwell['median']:.0f} 分", help="中央値")
                cc[1].metric("平均", f"{dwell['mean']:.0f} 分")
                cc[2].metric("短いほうから1/4", f"{dwell['q1']:.0f} 分", help="Q1（第1四分位）")
                cc[3].metric("長いほうから1/4", f"{dwell['q3']:.0f} 分", help="Q3（第3四分位）")
                st.caption(f"3時間以上いた人は全体の {dwell['pct_over_3h']:.1f}% です。")

            st.divider()
            st.markdown("#### 長くいる人ほど、多くのブースを回る？")
            reg = metrics.dwell_vs_booths_regression(active)
            fig2 = go.Figure()
            single = active[active["is_single"] == True]  # noqa: E712
            fig2.add_scatter(
                x=single["dwell_min"], y=single["n_booths"], mode="markers",
                name="単発訪問者（計算からは除外）",
                marker=dict(color="#cbd5e1", size=7),
                hovertemplate="単発訪問者<br>滞在 %{x:.0f} 分 ／ %{y} ブース<extra></extra>",
            )
            fig2.add_scatter(
                x=multi["dwell_min"], y=multi["n_booths"], mode="markers",
                name="2ブース以上を回った人",
                marker=dict(size=7, color="#1a56db"),
                hovertemplate="滞在 %{x:.0f} 分 ／ <b>%{y} ブース</b><extra></extra>",
            )
            slope = reg["slope_booths_per_hour"]
            if slope is not None:
                xs = np.linspace(multi["dwell_min"].min(), multi["dwell_min"].max(), 50)
                fig2.add_scatter(
                    x=xs, y=slope / 60 * xs + reg["intercept"], mode="lines",
                    name=f"平均的な傾向: 1時間あたり {slope:.2f} ブース",
                    line=dict(color="#111827", width=3),
                    hovertemplate="滞在 %{x:.0f} 分なら およそ %{y:.1f} ブース<extra></extra>",
                )
            styled(fig2, "滞在時間 × 回ったブース数", "点1つが参加者1人（1日ぶん）")
            fig2.update_layout(xaxis_title="滞在時間（分）", yaxis_title="訪問ブース数")
            show(fig2)
            howto(
                "点が右上がりに並んでいれば「長くいる人ほど多く回る」ということです。"
                "黒い直線はその平均的な傾き。<b>点にマウスを乗せると、その人の滞在時間とブース数が出ます。</b>"
            )
            if slope is not None:
                st.info(
                    f"📌 **1時間の滞在につき、およそ {slope:.2f} ブース回る**——"
                    "今年の規模を見積もるとき、いちばん多く使うことになる数字です。"
                )
            note(
                "この図の技術的な注記",
                "灰色の点（単発訪問者）は滞在時間が 0 分として記録されるため、回帰の計算からは除外しています"
                "（交絡3）。傾きは因果ではなく相関であり、「長くいるから多く回る」のか"
                "「多く回りたい人が長くいる」のかは、このデータからは区別できません。",
            )

    # --- 判断2 ---
    with tabs[1]:
        lead(
            "問い2: 今年、アプリには何件のデータが集まる？",
            "推薦アルゴリズムが成立するかどうかは、集まるデータ量で決まります。"
            "<b>下のつまみを動かして、今年の想定参加者数を入れてみてください。</b>",
        )
        expected = st.slider(
            "今年の想定参加者数（左右にドラッグ）", 50, 400, 175, step=25,
            help="去年の実績は延べ302名（2日間）。今年は金曜1日開催です",
        )
        median_booths = counts["median_n_booths"]
        if median_booths is None:
            st.info("この条件に当てはまるデータがありません。")
        else:
            est = expected * median_booths
            st.markdown(
                f"#### {expected} 名 × 1人あたり {median_booths:.1f} ブース = **およそ {est:.0f} 件**"
            )
            if est >= 1000:
                verdict(st, "協調フィルタリングが機能する程度のデータ量。推薦の作り込みは妥当。", "ok")
            elif est >= 300:
                verdict(st, "限界的。単純な人気度ベースの推薦に留めるべき。", "mid")
            else:
                verdict(st, "個人化推薦は成立しない。ルールベースで設計する。", "warn")
            if day is None:
                st.warning(
                    "⚠️ **いま「両日」を見ています。** 去年の総数は2日間の合計であり、"
                    "今年（金曜1日）の見積もりと直接比べてはいけません。"
                    "左の「① 見たい開催日」で **金曜 (10/10)** に絞ってから読んでください。"
                )

        with st.expander("📋 日ごとの基礎データを見る"):
            summary = metrics.daily_summary(p)
            st.dataframe(pd.DataFrame(summary["by_day"]), width="stretch")
            cc = st.columns(3)
            cc[0].metric("両日とも来た人", summary["n_both_days"])
            cc[1].metric("チェックイン0件の人", summary["n_zero_checkin"])
            cc[2].metric("カード未生成の人", summary["n_card_ungenerated"])

    # --- 判断3 ---
    with tabs[2]:
        lead(
            "問い3: 推薦機能は、そもそも全員に届く？",
            "アプリを一度も使わなかった人と、1ブースだけで終わった人には、"
            "推薦が働く余地がありません。<b>この割合が大きいほど、推薦より先に"
            "「使ってもらう」ことを解くべき</b>という判断になります。",
        )
        n_zero = len(zero_rows)
        n_single = int((active["is_single"] == True).sum())  # noqa: E712
        denom = n_people + n_zero
        if denom == 0:
            st.info("この条件に当てはまるデータがありません。")
        else:
            share = (n_zero + n_single) / denom * 100
            st.markdown(f"### 推薦がそもそも届かない人: **{share:.1f}%**")
            st.caption(
                f"内訳: 一度も使わなかった人 {n_zero} 名 ＋ 1ブースだけの人 {n_single} 名 ／ 全体 {denom} 名"
            )
            if share >= 40:
                verdict(st, "推薦機能より先に「使われない問題」を解くべき。オンボーディング設計が優先。", "warn")
            elif share >= 20:
                verdict(st, "推薦は有効だが、初回体験の改善と並行して進める。", "mid")
            else:
                verdict(st, "推薦機能に集中してよい。", "ok")
            if not include_zero:
                st.warning(
                    "⚠️ いま「チェックイン0件の参加者」を除外して見ているため、"
                    "この割合は実際より小さく出ています。左の「⚙️ 詳しい設定」で戻せます。"
                )

            hist = pd.concat([active["n_booths"], pd.Series([0] * n_zero)]) if include_zero else active["n_booths"]
            fig = go.Figure(
                go.Histogram(
                    x=hist, xbins=dict(start=-0.5, end=hist.max() + 0.5, size=1),
                    marker_color="#1a56db",
                    hovertemplate="%{x} ブース回った人<br><b>%{y} 人</b><extra></extra>",
                )
            )
            styled(fig, "何ブース回ったかの分布", f"横=回ったブース数 ／ 縦=人数（{day_choice}・0件から表示）")
            fig.update_layout(xaxis_title="訪問ブース数", yaxis_title="人数", bargap=0.05)
            show(fig)
            howto(
                "<b>いちばん左（0件）の棒がこの画面で最も重要です。</b>"
                "そこが高いほど「登録したのに使わなかった人」が多いことを意味します。"
                "棒にマウスを乗せると正確な人数が出ます。"
            )

    # --- 判断4 ---
    with tabs[3]:
        lead(
            "問い4: 人気ブースへの偏りは、対策にコストを割くほど大きい？",
            "一部のブースに人が集中していれば、アプリで回遊を誘導する価値があります。"
            "偏りが小さければ、そこに工数を使う必要はありません。",
        )
        ranking = metrics.booth_visit_ranking(v, booths)
        skew = metrics.booth_skew_stats(ranking)
        cc = st.columns(3)
        cc[0].metric("いちばん多いブース", f"{skew['max_visits']} 人")
        cc[1].metric("いちばん少ないブース", f"{skew['min_visits']} 人")
        cc[2].metric(
            "何倍の差か",
            f"{skew['ratio_max_to_min']:.1f} 倍"
            if skew["ratio_max_to_min"] is not None
            else f"差 {skew['diff_max_to_min']} 人",
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
            verdict(st, f"上位20%のブースが全訪問の {share:.1f}% を占める。倍率にかかわらず対策対象とする。", "warn")

        top_n = skew["top20pct_n_booths"]
        colors = ["#dc2626" if i < top_n else "#1a56db" for i in range(len(ranking))]
        fig = go.Figure(
            go.Bar(
                x=ranking["n_visits"][::-1], y=ranking["booth_short"][::-1],
                orientation="h", marker_color=colors[::-1],
                customdata=ranking["booth_name"][::-1],
                hovertemplate="<b>%{customdata}</b><br>訪問した人 %{x} 名<extra></extra>",
            )
        )
        styled(
            fig,
            "ブース別の訪問者数（多い順）",
            f"赤 = 上位20%（{top_n} ブース）"
            + (f"／その {top_n} ブースだけで全訪問の {share:.1f}% を占める" if share is not None else ""),
        )
        fig.update_layout(xaxis_title="訪問した人数", height=max(620, len(ranking) * 22 + 150))
        show(fig)
        howto(
            "上に行くほど人気のブースです。<b>棒にマウスを乗せると、ブースの正式名称と人数が出ます。</b>"
            "赤い棒が長く伸びているほど、一部のブースに人が集中していたことを意味します。"
        )

        st.divider()
        st.markdown("#### 来場者が「最初に」立ち寄ったブース")
        firsts = metrics.first_visit_distribution(v).merge(
            booths[["booth_id", "booth_short", "booth_name"]], on="booth_id", how="left"
        )
        if not firsts.empty:
            total = firsts["n_first_visits"].sum()
            top3 = firsts.head(3)["n_first_visits"].sum() / total * 100
            st.markdown(f"上位3ブースだけで、最初の1件の **{top3:.1f}%** を占めています。")
            head = firsts.head(10)
            fig = go.Figure(
                go.Bar(
                    x=head["n_first_visits"][::-1], y=head["booth_short"][::-1],
                    orientation="h", marker_color="#f59e0b",
                    customdata=head["booth_name"][::-1],
                    hovertemplate="<b>%{customdata}</b><br>ここから始めた人 %{x} 名<extra></extra>",
                )
            )
            styled(fig, "最初の1ブースになった回数 上位10", "会場に入って最初にチェックインした場所")
            fig.update_layout(xaxis_title="最初に訪れた人数", height=520)
            show(fig)
            howto(
                "ここが特定のブースに集中しているなら、<b>アプリが「最初の1件」を誘導する意味がある</b>"
                "ということです。ばらけていれば、来場者は自力で選べていることになります。"
            )

    # --- 判断5 ---
    with tabs[4]:
        lead(
            "問い5: 開場後、いつデータが溜まり始める？",
            "推薦はデータが無いと動きません。人がいつ来て、いつ会場にいたのかがわかれば、"
            "<b>推薦を何時から動かせばよいか</b>が決まります。",
        )
        if day is None:
            st.warning(
                "⚠️ 時間帯の話は、日を1日に絞って見てください。"
                "2日ぶんを重ねると時刻の意味が失われます（左の「① 見たい開催日」）。"
            )
        ts = metrics.time_series(v, active)
        if ts.empty:
            st.info("この条件に当てはまるデータがありません。")
        else:
            release = metrics.recommendation_fallback_release_time(v)
            fig = go.Figure()
            fig.add_bar(
                x=ts["bin"], y=ts["n_new_participants"], name="その時間に来はじめた人（新規）",
                marker_color="#93b4f5",
                hovertemplate="%{x|%H:%M} からの30分間<br>新しく来た人 <b>%{y} 名</b><extra></extra>",
            )
            fig.add_scatter(
                x=ts["bin"], y=ts["n_concurrent"], name="そのとき会場にいた人（同時滞在）",
                yaxis="y2", line=dict(color="#dc2626", width=4),
                hovertemplate="%{x|%H:%M}<br>会場にいた人 <b>%{y} 名</b><extra></extra>",
            )
            if release:
                fig.add_vline(
                    x=pd.Timestamp(release), line=dict(color="#059669", dash="dash"),
                    annotation_text="推薦が本格稼働した時刻", annotation_position="top",
                )
            styled(fig, "時間帯ごとの人の流れ", f"青い棒=新しく来た人 ／ 赤い線=そのとき会場にいた人（{day_choice}）")
            fig.update_layout(
                xaxis_title="時刻 (JST)",
                yaxis=dict(title="新しく来た人（名）"),
                yaxis2=dict(title="会場にいた人（名）", overlaying="y", side="right"),
            )
            show(fig)
            howto(
                "<b>青い棒＝左の目盛り、赤い線＝右の目盛り</b>です（目盛りが2本あることに注意）。"
                "青のピークが「人が入ってくる時間」、赤のピークが「いちばん混んでいた時間」。"
                "この2つのズレが、推薦をいつ動かすかの判断材料になります。"
                "マウスを乗せると、その時刻の人数が出ます。"
            )

            if release:
                st.success(
                    f"✅ チェックインした人が20名に達したのは **{pd.Timestamp(release):%H:%M}（JST）**。"
                    "今年、推薦をいつ起動するかを決める直接の根拠になります。"
                )
            else:
                st.info("チェックインした人が20名に達しませんでした（推薦は最後まで本格稼働していない）。")

            peak_new = ts.loc[ts["n_new_participants"].idxmax(), "bin"]
            opening = ts["bin"].min()
            elapsed = (peak_new - opening).total_seconds() / 3600
            st.markdown(f"##### 来場のピーク: {peak_new:%H:%M}（最初のチェックインから {elapsed:.1f} 時間後）")
            if ts["n_new_participants"].std() < ts["n_new_participants"].mean() * 0.5:
                verdict(st, "流入は終日ほぼ平坦。推薦は常に不完全なデータで動く覚悟が要る。", "mid")
            elif elapsed <= 1:
                verdict(st, "開場後1時間以内に流入がピーク。「開場直後にデータを集めきる」設計が成立する。", "ok")
            else:
                verdict(st, "ピークが中盤以降。開場直後は推薦に使えるデータがない。初期はランダム提示で凌ぐ設計が必要。", "warn")

            note(
                "赤い線（同時滞在人数）の限界",
                "同時滞在人数は、最初のチェックインより前と、最後のチェックインより後が見えません。"
                "実際の入退場より短く出ているため、**線の両端はあてにできません**。",
            )

    # --- 推薦マスの効果 ---
    with tabs[5]:
        lead(
            "去年の推薦は、実際に効いていた？",
            "ビンゴカードには「アプリが選んだマス（推薦）」と「そうでないマス」がありました。"
            "どちらがよくチェックインされたかを比べれば、推薦の効き目が測れます。",
        )
        effect = metrics.recommendation_effect(p)
        rec, rnd = effect["recommended_hit_rate"], effect["random_hit_rate"]
        if rec is None or rnd is None:
            st.info("ビンゴカードのデータがありません。")
        else:
            cc = st.columns(2)
            cc[0].metric(
                "推薦マスが踏まれた率", f"{rec * 100:.1f}%", f"{(rec - rnd) * 100:+.1f} pt",
                help="アプリが選んだマスのうち、実際にチェックインされた割合。下の増減はランダムマスとの差",
            )
            cc[1].metric("ランダムマスが踏まれた率", f"{rnd * 100:.1f}%", help="推薦以外のマスの割合")
            if rec > rnd:
                verdict(st, "去年の推薦は機能していた。同方式を踏襲してよい。", "ok")
            else:
                verdict(st, "去年の推薦は効いていない。方式そのものを見直す必要がある。", "warn")
            st.caption(f"推薦マス {effect['n_recommended_slots']} 枠 ／ ランダムマス {effect['n_random_slots']} 枠で比較")
            note(
                "この結論をそのまま信じてはいけない理由",
                "推薦マスは中央4マス（position 5/6/9/10）に**固定配置**されていたため、"
                "「位置が目立つから踏まれた」のか「推薦だから踏まれた」のかを区別できません。"
                "位置の効果と推薦の効果が交絡しています。",
            )

        st.divider()
        st.markdown("#### ビンゴはどこまで進んだ？")
        bc = metrics.bingo_completion(p)
        vf = metrics.vote_finalization_rate(p)
        cc = st.columns(3)
        cc[0].metric("埋まったマス数（真ん中の人）", f"{bc['median_n_card_hit']:.0f} / 16")
        cc[1].metric("完走（4ライン）した人の割合", f"{bc['pct_completed_4_lines']:.1f}%")
        cc[2].metric(
            "投票を確定した人の割合", f"{vf['vote_finalized_rate_pct']:.1f}%",
            help="事後アンケートが存在しないため、今年の評価入力の回収率を見積もる代理指標として使う（F-4）",
        )

    # --- クールタイム ---
    with tabs[6]:
        lead(
            "すべての数字に効いてくる前提: チェックインの間隔制限",
            "去年のアプリには「一度チェックインしたら次まで待つ」制限（クールタイム）があり、"
            "しかも<b>期間中に設定が変更されました</b>。参加者が制限いっぱいで回っていた場合、"
            "回遊ペースの数字は「アプリの上限」を測っているだけになります。"
            "<b>ほかのタブを読む前に、ここを見てください。</b>",
        )
        floor = metrics.cooldown_floor(v)
        if floor.empty:
            st.info("チェックイン間隔のデータがありません。")
        else:
            sat = metrics.cooldown_saturation(v, floor)
            st.markdown(f"### 制限いっぱいで回っていた割合: **{sat * 100:.1f}%**")
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
            fig.add_scatter(
                x=floor["bin"], y=floor["min_sec"], mode="lines+markers", name="いちばん短かった間隔",
                line=dict(color="#1a56db"),
                hovertemplate="%{x|%H:%M}<br>最短 %{y:.0f} 秒<extra></extra>",
            )
            fig.add_scatter(
                x=floor["bin"], y=floor["p5_sec"], mode="lines+markers", name="短いほうから5%の間隔",
                line=dict(color="#f59e0b"),
                hovertemplate="%{x|%H:%M}<br>5パーセンタイル %{y:.0f} 秒<extra></extra>",
            )
            styled(fig, "チェックイン間隔の「下限」の推移", "縦軸は対数目盛り（1目盛りで10倍）")
            fig.update_layout(xaxis_title="時刻 (JST)", yaxis_title="間隔（秒）", yaxis_type="log")
            show(fig)
            howto(
                "線が<b>階段のようにガクッと下がっている場所が、設定が変更された時刻</b>です。"
                "線が平らな区間は、その値が制限値そのものだったことを意味します。"
            )
            note(
                "設定変更の時刻について（記録と食い違う）",
                "git履歴では 180秒 → 60秒（初日20:08のコミット）ですが、運営の記憶とは食い違います。"
                "コミット時刻はデプロイ時刻ではないため、**データに刻まれた床（この図）を正とします**。",
            )

    # --- 除外候補 ---
    with tabs[7]:
        lead(
            "運営・出展者のアカウントを見つける（担当者向け）",
            "運営や出展者のアカウントが混ざっていると、来場者の平均像がゆがみます。"
            "ただし<b>自動では除外しません</b>——一般参加者を誤って消すリスクのほうが大きいためです。"
            "下の一覧を人の目で確認してください。",
        )
        open_hour = st.slider(
            "開場時刻（この時刻より前にチェックインした人を候補として拾う）", 6, 12, 9,
            help="開場前に動いている人は、運営・出展者である可能性が高い",
        )
        import build_tables

        cand = build_tables.detect_staff_candidates(v, booths, open_hour_jst=open_hour)
        if cand.empty:
            st.success("✅ 候補はありません。")
        else:
            st.dataframe(cand, width="stretch")
            st.info(
                "除外すると決めた pid を、左サイドバーの「⚙️ 詳しい設定」→「除外する pid」に"
                "1行ずつ貼り付けてください。貼り付けると全タブの数字が再計算されます。"
            )


if __name__ == "__main__":
    # 想定外の例外は、担当者へそのまま転送できるレポート画面に変換する
    report.guarded(main)
