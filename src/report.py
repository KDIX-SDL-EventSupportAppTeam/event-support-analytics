"""不具合レポート画面。

想定する利用者は非エンジニアである。画面が止まったりデータが出てこなかったときに、
**何が起きたかを説明できなくても、そのまま担当者へ転送すれば原因がわかる文面**を
自動で組み立てて見せる。

レポートに入れてよいもの:
    起きたエラーの種類と発生場所、実行環境（Cloud Run のリビジョン等）、
    そのとき選んでいた絞り込み条件。

入れてはいけないもの:
    参加者の行動データそのもの。集計値も含めない。
    レポートはチャット等で転送されるため、data/ の中身が外へ出る経路にしない。
"""

from __future__ import annotations

import datetime as dt
import os
import platform
import sys
import traceback
import uuid

import streamlit as st

CONTEXT_KEY = "_report_context"
_ID_KEY = "_report_id"

# 「この画面を共有した担当者」の連絡先。Cloud Run では環境変数で指定できる
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "この画面を共有した担当者")

JST = dt.timezone(dt.timedelta(hours=9))


def set_context(**values) -> None:
    """レポートに載せる「そのとき何を見ていたか」を記録する。"""
    st.session_state[CONTEXT_KEY] = {k: v for k, v in values.items()}


def _report_id() -> str:
    if _ID_KEY not in st.session_state:
        st.session_state[_ID_KEY] = uuid.uuid4().hex[:8].upper()
    return str(st.session_state[_ID_KEY])


def _environment() -> dict[str, str]:
    env = {
        "発生時刻 (JST)": dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        "Python": platform.python_version(),
        "Streamlit": st.__version__,
        "OS": platform.platform(),
        "作業ディレクトリ": os.getcwd(),
    }
    # Cloud Run が自動で入れる環境変数。どのリビジョンで起きたかの特定に使う
    for key, label in (("K_SERVICE", "サービス名"), ("K_REVISION", "リビジョン")):
        if os.environ.get(key):
            env[label] = os.environ[key]
    return env


def build_report(summary: str, detail: str = "") -> str:
    """担当者へそのまま送れるテキストを組み立てる。"""
    lines = [
        "===== プロトフェス ダッシュボード 不具合レポート =====",
        f"レポート番号: {_report_id()}",
        f"症状: {summary}",
        "",
        "--- 実行環境 ---",
    ]
    lines += [f"{k}: {v}" for k, v in _environment().items()]

    context = st.session_state.get(CONTEXT_KEY) or {}
    if context:
        lines += ["", "--- そのとき選んでいた条件 ---"]
        lines += [f"{k}: {v}" for k, v in context.items()]

    if detail:
        lines += ["", "--- 技術的な詳細（担当者向け）---", detail.rstrip()]

    lines += ["", "===== ここまで ====="]
    return "\n".join(lines)


def show_error_screen(summary: str, detail: str = "", advice: str = "") -> None:
    """止まってしまったことを伝え、送るべき文面を渡す。この関数は戻らない。"""
    st.error(f"## 🛑 画面を表示できませんでした\n\n{summary}")
    st.markdown(
        "**あなたの操作が原因ではない可能性が高いです。** まずは次を試してください。\n\n"
        "1. ブラウザを再読み込みする（Windows: `Ctrl` + `R` ／ Mac: `⌘` + `R`）\n"
        "2. それでも直らなければ、**下の枠の内容をコピーして "
        f"{SUPPORT_CONTACT} に送ってください**"
    )
    if advice:
        st.info(advice)

    report = build_report(summary, detail)
    st.markdown("#### 📋 この内容をコピーして送ってください")
    st.caption("枠の右上にあるコピーのアイコンを押すと、全文がコピーされます。")
    st.code(report, language="text")
    st.download_button(
        "💾 ファイルとして保存する（メール添付用）",
        data=report.encode("utf-8"),
        file_name=f"protofes-dashboard-{_report_id()}.txt",
        mime="text/plain",
        type="primary",
    )
    st.caption(
        f"レポート番号 **{_report_id()}** を口頭で伝えるだけでも、担当者はこの画面を特定できます。"
    )
    st.stop()


def show_data_missing_screen(message: str, advice: str = "") -> None:
    """データが届いていない場合。エラーとは原因が違うので文面を分ける。"""
    st.error(f"## 📭 表示するデータがありません\n\n{message}")
    st.markdown(
        "画面そのものは動いていますが、**中身のデータが読み込めていません**。"
        f"下の枠の内容をコピーして {SUPPORT_CONTACT} に送ってください。"
    )
    if advice:
        st.info(advice)
    report = build_report("データが読み込めない", message)
    st.code(report, language="text")
    st.download_button(
        "💾 ファイルとして保存する（メール添付用）",
        data=report.encode("utf-8"),
        file_name=f"protofes-dashboard-{_report_id()}.txt",
        mime="text/plain",
        type="primary",
    )
    st.stop()


def guarded(main) -> None:
    """main() を実行し、想定外の例外をレポート画面に変える。"""
    try:
        main()
    except BaseException as exc:  # noqa: BLE001
        # st.stop() / st.rerun() は内部的に例外で制御フローを止めている。
        # これらは不具合ではないので、そのまま通す。
        if type(exc).__name__ in {"StopException", "RerunException"}:
            raise
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        print(detail, file=sys.stderr)  # Cloud Logging にも残す
        show_error_screen(
            f"想定していない問題が起きて、途中で止まりました（{type(exc).__name__}）。",
            detail=detail,
        )
