"""統合アプリの入口。3つの画面を左のメニューで行き来する。**デプロイはこれを起動する。**

    streamlit run src/app.py

| 画面 | 対象 | 仕様 |
|---|---|---|
| 去年の行動データ | 2025年（第3回） | specs/analytics-pipeline/ |
| 推薦の当日監視 | 2026年（第4回）当日 | specs/recommendation-evaluation/03 |
| 推薦の事後分析 | 2026年（第4回）事後 | specs/recommendation-evaluation/04 |

**合言葉はここで1回だけ確認する。** 認証を通るまで `st.navigation` を組み立てないので、
メニュー自体が出ない = どの画面にも入れない。画面を追加したときに認証を付け忘れる事故を
構造で防ぐため、個別画面ではなくこの入口で止める
（各画面の `main()` も単体起動のために `auth.require_password()` を呼ぶが、
合言葉が通ったあとは素通りする）。

当日の監視画面は 45 秒ごとに自動更新される。**メニューで別の画面へ移ると更新は止まる。**
当日はこの画面を開いたままにしておくこと（仕様 03 §6）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

# set_page_config は最初の st コマンドである必要があるため、画面の import より先に呼ぶ。
# 各画面の page_setup.configure() はこれを検知して素通りする。
st.set_page_config(page_title="プロトフェス 分析・監視", page_icon="📊", layout="wide")

import auth  # noqa: E402
import dashboard  # noqa: E402
import live_dashboard  # noqa: E402
import post_analysis  # noqa: E402


def main() -> None:
    auth.require_password()  # 合言葉が未設定のローカル実行では素通りする

    pages = [
        st.Page(dashboard.main, title="去年の行動データ", icon="📊",
                url_path="last-year", default=True),
        st.Page(live_dashboard.main, title="推薦の当日監視", icon="🚦", url_path="live"),
        st.Page(post_analysis.main, title="推薦の事後分析", icon="📈", url_path="post"),
    ]
    st.sidebar.markdown("### 画面")
    st.navigation(pages, position="sidebar").run()


if __name__ == "__main__":
    main()
