"""今年（2026年）データの取得口。

仕様: docs/specs/recommendation-evaluation/02-data-source.md

このリポジトリは去年 Firestore の一度限りのダンプを扱ってきた（dump_firestore.py）。
今年は MySQL であり、当日はライブで読む必要がある。ただし **接続経路が未確定**
（仕様 E-1 / 02 §4）。決まるまで当日ダッシュボードは本番接続できない。

そこで取得口を差し替え可能にする:

- `DumpSource`     … イベント後のダンプ1回ぶん（CSV/Parquet ディレクトリ）。事後分析はこれで足りる
- `SqlSource`      … MySQL への接続。経路が決まったら実装する（現在は NotImplementedError）
- `SynthSource`    … リハーサル用の合成データ（synth_rec_data.py が書き出したディレクトリ）

いずれも同じ `table(name)` を返すので、metrics 層・画面層は取得口を意識しない。

`/ops/state`（推薦エンジン）は **别扱い**。落ちていても画面全体を止めない
（02 §1）。`OpsStateClient.fetch()` は失敗時に None を返す。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

import pandas as pd

# 当日の監視に使うテーブル（02 §2）。取得してはならない列は最初から SELECT しない。
LIVE_TABLES: dict[str, tuple[str, ...]] = {
    "card_unlock_events": (
        "user_id",
        "strategy",
        "phase",
        "decision_table_size",
        "global_checkin_count",
        "created_at",
    ),
    "check_ins": ("id", "user_id", "booth_id", "cell_id", "visit_order", "checked_in_at"),
    "booth_ratings": ("checkin_id", "rating", "scale", "rated_at"),
    "recommendation_scores": (
        "user_id",
        "booth_id",
        "was_assigned",
        "score",
        "rank_in_event",
        "interest_match",
        "attributes",
        "reason_payload",
        "created_at",
    ),
    "bingo_cells": ("user_id", "booth_id", "is_revealed", "is_achieved", "source"),
    "users": ("id", "role"),
}

# 事後の分析で追加で使うテーブル（02 §2）
POST_TABLES: dict[str, tuple[str, ...]] = {
    "user_survey_answers": ("user_id", "age_range", "occupation", "industry", "custom_answers"),
    "booths": ("id", "name", "category_id"),
    "categories": ("id", "name"),
    "booth_tags": ("booth_id", "tag"),
}

# 取得してはならない列（02 §2「取得してはならないもの」）。防御的に明示する。
FORBIDDEN_COLUMNS = {("users", "email"), ("users", "password_hash")}

_PARTICIPANT_ROLE = "participant"


class Source(Protocol):
    """テーブル1枚を DataFrame で返すだけの口。"""

    def table(self, name: str) -> pd.DataFrame: ...


class DumpSource:
    """イベント後のダンプディレクトリ。`<name>.parquet` を優先し、無ければ `<name>.csv`。"""

    # 文字列で来てもパースしておきたい日時列
    _DATETIME_COLS = {"created_at", "checked_in_at", "rated_at"}

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise FileNotFoundError(f"ダンプディレクトリが無い: {self.root}")

    def table(self, name: str) -> pd.DataFrame:
        parquet = self.root / f"{name}.parquet"
        csv = self.root / f"{name}.csv"
        if parquet.exists():
            df = pd.read_parquet(parquet)
        elif csv.exists():
            df = pd.read_csv(csv)
        else:
            raise FileNotFoundError(f"{name} が {self.root} に無い（.parquet / .csv）")
        for col in self._DATETIME_COLS & set(df.columns):
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
        _reject_forbidden(name, df.columns)
        return df


class SynthSource(DumpSource):
    """合成データ。形式は DumpSource と同じ。型を分けておくと画面に「合成」と出せる。"""


class SqlSource:
    """MySQL 接続。**接続経路が未確定のため未実装**（仕様 E-1 / 02 §4）。

    経路が決まったら、ここに 1リクエスト=1SQL・トランザクション無し（さくらプロキシ）
    の制約を織り込んで実装する。`LIVE_TABLES` の列だけを SELECT すること。
    """

    def __init__(self, *_args, **_kwargs) -> None:
        raise NotImplementedError(
            "今年の MySQL への接続経路が未確定（docs/specs/recommendation-evaluation/02-data-source.md §4, E-1）。"
            "決まるまでは DumpSource / SynthSource を使う。"
        )

    def table(self, name: str) -> pd.DataFrame:  # pragma: no cover - 到達しない
        raise NotImplementedError


def _reject_forbidden(name: str, columns) -> None:
    hit = sorted(c for c in columns if (name, c) in FORBIDDEN_COLUMNS)
    if hit:
        raise ValueError(f"{name} に取得禁止の列が含まれる: {hit}（02 §2）。取得側で除外すること")


def participants_only(df: pd.DataFrame, users: pd.DataFrame, *, user_col: str = "user_id") -> pd.DataFrame:
    """`role <> 'participant'` を全集計から除外する（02 §2）。

    users に role が無い、または該当ユーザーが users に居ない場合は **除外しない**
    （落とすより残す方が監視では安全側）。
    """
    if "role" not in users.columns or "id" not in users.columns:
        return df
    staff = set(users.loc[users["role"].astype(str) != _PARTICIPANT_ROLE, "id"])
    if not staff:
        return df
    return df[~df[user_col].isin(staff)].copy()


class OpsStateClient:
    """推薦エンジンの `/ops/state`。**取得できなくても画面を止めない**（02 §1）。"""

    def __init__(self, base_url: str | None, timeout_sec: float = 3.0) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.timeout_sec = timeout_sec

    def fetch(self) -> dict | None:
        """成功時 dict、失敗時 None。例外は投げない。"""
        if not self.base_url:
            return None
        url = f"{self.base_url}/ops/state"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_sec) as resp:  # noqa: S310
                if resp.status != 200:
                    return None
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return None
