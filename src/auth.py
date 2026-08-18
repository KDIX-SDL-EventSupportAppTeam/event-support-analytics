"""合言葉による簡易アクセス制限。

同じ GCP プロジェクトに所属していない運営メンバーへ画面を共有するための、
**最低限の**入口である。想定する脅威は「URL が偶然知られて中身を見られる」ことまでで、
本気の攻撃者を防ぐものではない。個人を特定できる情報は data/ の時点で
仮名化済み（privacy-policy.md）であることを前提に、この強度で運用する。

合言葉の渡し方:
    Cloud Run  … Secret Manager の値を環境変数 DASHBOARD_PASSWORD に注入する
    ローカル    … 環境変数を設定しない。認証なしで起動する（開発の邪魔をしない）

DASHBOARD_AUTH_REQUIRED=1 が設定されている場合（コンテナ内は常にそう）、
合言葉が未設定なら画面を出さずに停止する。設定漏れで全世界に公開されることを防ぐため。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

import streamlit as st

ENV_PASSWORD = "DASHBOARD_PASSWORD"
ENV_REQUIRED = "DASHBOARD_AUTH_REQUIRED"

MAX_ATTEMPTS = 10  # これを超えたら、そのセッションでは入力欄を出さない
WRONG_ANSWER_DELAY_SEC = 1.0  # 総当たりの速度を落とす


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def configured_password() -> str | None:
    """設定された合言葉。未設定なら None。"""
    from_env = os.environ.get(ENV_PASSWORD)
    if from_env:
        return from_env
    try:  # ローカルで .streamlit/secrets.toml を使いたい場合の補助
        value = st.secrets.get(ENV_PASSWORD)  # type: ignore[union-attr]
    except Exception:  # secrets.toml が無い環境では例外になる
        return None
    return str(value) if value else None


def _gate(password: str) -> None:
    st.markdown("## 🔒 関係者用ページ")
    st.markdown(
        "プロトフェスの運営メンバー向けの画面です。"
        "共有された**合言葉**を入力してください。"
    )
    attempts = st.session_state.get("_auth_attempts", 0)
    if attempts >= MAX_ATTEMPTS:
        st.error("入力の失敗が続いたため、この画面を閉じました。ブラウザを開き直してください。")
        st.stop()

    with st.form("auth"):
        entered = st.text_input("合言葉", type="password", placeholder="共有された合言葉")
        submitted = st.form_submit_button("ページを開く", type="primary")

    if submitted:
        # compare_digest は非ASCII文字列を直接比較できない。合言葉に日本語を
        # 使われても壊れないよう、ハッシュ同士を定数時間で比較する。
        if hmac.compare_digest(_digest(entered.strip()), _digest(password)):
            st.session_state["_auth_ok"] = True
            st.session_state.pop("_auth_attempts", None)
            st.rerun()
        st.session_state["_auth_attempts"] = attempts + 1
        time.sleep(WRONG_ANSWER_DELAY_SEC)
        st.error("合言葉が違います。共有元に確認してください。")

    st.caption("合言葉がわからない場合は、この画面を共有した担当者に聞いてください。")
    st.stop()


def require_password() -> None:
    """合言葉が通るまで、以降の描画を行わない。

    合言葉が未設定のローカル実行では何もしない（テストと開発をそのまま通す）。
    """
    password = configured_password()
    if password is None:
        if os.environ.get(ENV_REQUIRED) == "1":
            st.error(
                f"合言葉が設定されていません（環境変数 `{ENV_PASSWORD}`）。\n\n"
                "安全のため画面を表示しません。Cloud Run の場合は Secret Manager の"
                "シークレットが正しく紐づいているか確認してください。"
            )
            st.stop()
        return

    if st.session_state.get("_auth_ok"):
        return
    _gate(password)
