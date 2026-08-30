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
