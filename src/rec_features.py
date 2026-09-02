"""条件属性・`interest_match` の定義は **推薦リポジトリから import する**。コピーしない。

仕様: docs/specs/recommendation-evaluation/README.md「推薦エンジン側の仕様との対応」
      docs/specs/recommendation-evaluation/04-post-analysis.md §7
根拠: event-support-server ADR 0004（「分析で使った定義と本番の推薦が違う」事故を構造で防ぐ）

正本は `event-support-recommend` の `src/event_support_recommend/features/`。
向こうの `features/__init__.py` に「★event-support-analytics が import する公開 API」と
明記されており、シグネチャと戻り値は公開 API として扱われている
（変更時は向こうの `docs/specs/02-features.md` §6 のバージョニング規律に従う）。

`REC_FEATURES_PATH` に **推薦リポジトリのルート**（または その `src/`）を渡す。
インストール済み（`pip install -e`）なら何も渡さなくても import できる。

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

MODULE = "event_support_recommend.features"
ENV_PATH = "REC_FEATURES_PATH"

_HINT = (
    f"推薦リポジトリの {MODULE} を import できない。"
    f"環境変数 {ENV_PATH} に event-support-recommend のルート（または その src/）を設定するか、"
    "推薦リポジトリを pip install -e でインストールする。"
    "定義をこのリポジトリへコピーしないこと（README「推薦エンジン側の仕様との対応」）。"
)


def _candidate_roots(base: Path) -> list[Path]:
    """`src` レイアウトを吸収する。リポジトリのルートでも src/ でも受ける。"""
    return [base / "src", base]


def load_features():
    """`event-support-recommend` の `features` パッケージを返す。

    見つからなければ `ModuleNotFoundError`（対処方法つき）。**フォールバックで
    定義を自前実装しない** —— それをやると ADR 0004 が防ぎたい事故そのものになる。
    """
    extra = os.environ.get(ENV_PATH)
    if extra:
        base = Path(extra)
        root = next((r for r in _candidate_roots(base)
                     if (r / "event_support_recommend" / "features").is_dir()), None)
        if root is None:
            raise ModuleNotFoundError(
                f"{base} 配下に event_support_recommend/features/ が無い。{_HINT}")
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
    try:
        return importlib.import_module(MODULE)
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(_HINT) from exc


def features_available() -> bool:
    try:
        load_features()
        return True
    except ModuleNotFoundError:
        return False
