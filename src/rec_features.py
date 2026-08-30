"""条件属性・`interest_match` の定義は **推薦リポジトリから import する**。コピーしない。

仕様: docs/specs/recommendation-evaluation/README.md「推薦エンジン側の仕様との対応」
      docs/specs/recommendation-evaluation/04-post-analysis.md §7
根拠: event-support-server ADR 0004（「分析で使った定義と本番の推薦が違う」事故を構造で防ぐ）

正本は `event-support-recommend/features/`。このリポジトリはそれを import して使う。
`event-support-recommend` を PYTHONPATH に通すか、`REC_FEATURES_PATH` にパスを渡す。

当日・事後を通じて **`interest_match` は再計算しない**。推薦した瞬間の値が
`recommendation_scores.interest_match` に凍結されている（04 §4）。カテゴリは運営が
当日でも編集できるため、後から計算すると値がずれる。この seam が要るのは主に
事後の「規則一覧」（04 §5）で条件属性を人間可読な形に整えるときだけ。
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

_HINT = (
    "推薦リポジトリの features/ を import できない。"
    "event-support-recommend を PYTHONPATH に通すか、環境変数 REC_FEATURES_PATH に "
    "そのリポジトリのルート（features/ の親）を設定する。"
    "定義をこのリポジトリへコピーしないこと（README「推薦エンジン側の仕様との対応」）。"
)


def load_features():
    """`event-support-recommend` の `features` パッケージを返す。

    見つからなければ `ModuleNotFoundError`（対処方法つき）。**フォールバックで
    定義を自前実装しない** —— それをやると ADR 0004 が防ぎたい事故そのものになる。
    """
    extra = os.environ.get("REC_FEATURES_PATH")
    if extra and extra not in sys.path:
        p = Path(extra)
        if not (p / "features").is_dir():
            raise ModuleNotFoundError(f"{p} に features/ が無い。{_HINT}")
        sys.path.insert(0, str(p))
    try:
        return importlib.import_module("features")
    except ModuleNotFoundError as exc:  # pragma: no cover - 環境依存
        raise ModuleNotFoundError(_HINT) from exc


def features_available() -> bool:
    try:
        load_features()
        return True
    except ModuleNotFoundError:
        return False
