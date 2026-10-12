"""ページ設定の共有ヘルパ。

各画面は2通りの入口を持つ。

1. **統合アプリ**（`streamlit run src/app.py`）… デプロイはこちら。
   入口が `st.set_page_config()` を1回呼び、`st.navigation` で3画面を行き来する
2. **単体起動**（`streamlit run src/live_dashboard.py` など）… 開発とテスト用

`st.set_page_config()` は1回しか呼べない。統合アプリ経由では入口が既に呼んでいるため、
各画面が同じ呼び出しをすると例外になる。`configure()` はそれを吸収し、
どちらの入口でも同じコードが動くようにする。
"""

from __future__ import annotations

import streamlit as st


def configure(*, page_title: str, page_icon: str, layout: str = "wide") -> None:
    """単体起動ならページ設定を行い、統合アプリ経由（設定済み）なら何もしない。"""
    try:
        st.set_page_config(page_title=page_title, page_icon=page_icon, layout=layout)
    except st.errors.StreamlitAPIException:
        # 統合アプリの入口が既に設定済み。その設定を尊重して素通りする
        pass
    apply_style()


#: 全画面共通の見た目。**色は固定せず、状態色（--c）を薄く重ねるか枠線に使うだけにする。**
#: 背景を明るい色で固定すると、ダークテーマで文字が読めなくなる（信号機で実際に起きた）。
_STYLE = """
<style>
.block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1400px; }
h1 { font-size: 1.9rem !important; letter-spacing: .01em; }
h3 { margin-top: .6rem !important; }
[data-testid="stCaptionContainer"] { opacity: .8; }

/* タブ */
.stTabs [data-baseweb="tab-list"] { gap: .25rem; border-bottom: 1px solid rgba(128,128,128,.25); }
.stTabs [data-baseweb="tab"] { padding: .55rem 1rem; font-size: 1rem; font-weight: 600; }

/* 数値カード */
[data-testid="stMetric"] {
  border: 1px solid rgba(128,128,128,.25); border-radius: 12px;
  padding: .75rem 1rem; background: rgba(128,128,128,.06);
}
[data-testid="stMetricLabel"] p { font-size: .85rem; opacity: .85; }
[data-testid="stMetricValue"] { font-size: 1.7rem; font-weight: 700; }

/* 表・グラフ */
[data-testid="stDataFrame"], [data-testid="stPlotlyChart"] {
  border: 1px solid rgba(128,128,128,.2); border-radius: 12px; overflow: hidden;
}

/* 信号機 */
.sig-banner {
  display: flex; gap: 1rem; align-items: center; padding: 1rem 1.25rem; margin: .25rem 0 1rem;
  border-radius: 14px; border: 1px solid var(--c);
  background: color-mix(in srgb, var(--c) 14%, transparent);
}
.sig-banner-mark { font-size: 2.2rem; line-height: 1; }
.sig-banner-head { font-size: 1.35rem; font-weight: 800; }
.sig-banner-sub { font-size: .95rem; opacity: .85; margin-top: .15rem; }
.sig-group {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: .25rem .75rem;
  margin: 1.2rem 0 .5rem; padding-bottom: .3rem; border-bottom: 1px solid rgba(128,128,128,.25);
}
.sig-group span { font-size: 1.1rem; font-weight: 700; }
.sig-group small { opacity: .7; font-size: .85rem; }
.sig-card {
  border: 1px solid rgba(128,128,128,.25); border-left: 6px solid var(--c); border-radius: 12px;
  padding: .8rem 1rem; margin-bottom: .75rem;
  background: color-mix(in srgb, var(--c) 7%, transparent);
}
.sig-head { display: flex; justify-content: space-between; align-items: center; gap: .5rem; }
.sig-title { font-weight: 700; font-size: 1rem; }
.sig-pill {
  flex-shrink: 0; font-size: .78rem; font-weight: 700; padding: .1rem .55rem; border-radius: 999px;
  border: 1px solid var(--c); color: var(--c);
}
.sig-what { font-size: .8rem; opacity: .72; margin-top: .2rem; line-height: 1.45; }
.sig-value { font-size: 1.9rem; font-weight: 800; color: var(--c); margin: .3rem 0 .1rem; }
.sig-detail { font-size: .85rem; opacity: .85; }
.sig-tech { opacity: .55; font-size: .75rem; }
.sig-action { margin-top: .45rem; font-size: .88rem; padding-top: .4rem; border-top: 1px dashed rgba(128,128,128,.3); }
.sig-calm { opacity: .6; font-size: .8rem; }
</style>
"""


def apply_style() -> None:
    """共通の CSS を差し込む。統合アプリの入口と単体起動の両方から呼ぶ（何度呼んでもよい）。"""
    st.markdown(_STYLE, unsafe_allow_html=True)
