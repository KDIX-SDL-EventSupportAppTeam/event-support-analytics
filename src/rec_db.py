"""今年（2026年）データの取得口。

仕様: docs/specs/recommendation-evaluation/02-data-source.md

このリポジトリは去年 Firestore の一度限りのダンプを扱ってきた（dump_firestore.py）。
今年は MySQL であり、当日はライブで読む必要がある。接続経路は **ADR 0001（案A′）** で決まった:
さくら上の PHP ラッパー API に読み取り専用の口を1つ足し、その鍵だけをここに配る。
**MySQL への直接接続はできない**（さくら Standard が許さない）。

そこで取得口を差し替え可能にする:

- `DumpSource`     … イベント後のダンプ1回ぶん（CSV/Parquet ディレクトリ）。事後分析はこれで足りる
- `SqlSource`      … 読み取り専用プロキシ経由の本番 MySQL（ADR 0001）。環境変数を設定すれば動く
- `SynthSource`    … リハーサル用の合成データ（synth_rec_data.py が書き出したディレクトリ）

いずれも同じ `table(name)` を返すので、metrics 層・画面層は取得口を意識しない。

`/ops/state`（推薦エンジン）は **别扱い**。落ちていても画面全体を止めない
（02 §1）。`OpsStateClient.fetch()` は失敗時に None を返す。
本番の `/ops/*` は `X-Ops-Token`（`RECOMMEND_OPS_TOKEN`）で保護されている（推薦側 ADR 0008）。
"""

from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple, Protocol

import pandas as pd

# 当日の監視に使うテーブルと列（02 §2）。取得してはならない列は最初から SELECT しない。
#
# 列は event-support-server の db/create-tables.sql に合わせてある。
# **card_unlock_events と bingo_cells は user_id を持たない**（card_id 経由で bingo_cards にある）。
# ここを取り違えると、存在しない列を SELECT して当日に落ちる。
LIVE_TABLES: dict[str, tuple[str, ...]] = {
    "bingo_cards": ("id", "event_id", "user_id"),
    "card_unlock_events": (
        "id",
        "card_id",
        "phase",
        "strategy",
        "decision_table_size",
        "global_checkin_count",
        "created_at",
    ),
    "check_ins": ("id", "user_id", "booth_id", "event_id", "cell_id", "visit_order", "checked_in_at"),
    "booth_ratings": ("checkin_id", "user_id", "booth_id", "event_id", "rating", "scale", "rated_at"),
    "recommendation_scores": (
        "id",
        "unlock_event_id",
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
    "bingo_cells": ("id", "card_id", "position", "booth_id", "is_revealed", "is_achieved", "source"),
    "users": ("id", "role"),
}

# card_id しか持たないテーブル。読み込み後に bingo_cards から user_id / event_id を補う。
CARD_KEYED_TABLES = ("card_unlock_events", "bingo_cells")

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
    """さくらの**読み取り専用プロキシ**経由で今年の MySQL を読む（ADR 0001 案A′）。

    `POST <base_url>` に `{"sql": ..., "params": [...]}` を投げ、
    `{"rows": [...], "affectedRows": n, "insertId": ...}` を受ける
    （契約は `event-support-server/src/db/http-proxy.ts`）。

    制約:

    - **1リクエスト = 1SQL。** トランザクションも行ロックも無い
    - **エラーはすべて HTTP 500 に潰れる。** MySQL のエラーコードは取れないので、
      「何を試したか」（どのテーブルか）だけを例外に載せる。**SQL 本文と鍵は載せない**

    安全側の担保（権限が最終的な保証だが、クライアント側でも二重に持つ）:

    - テーブル名は `LIVE_TABLES` / `POST_TABLES` のキーのみ。任意の文字列を SQL に入れない
    - 列も同じ定義から組み立てる。`SELECT *` を書かないので `FORBIDDEN_COLUMNS` は構造的に要求できない
    - 組み立てた SQL が `SELECT` で始まらなければ送信前に拒否する
    """

    #: 読み取り専用の口の URL / 鍵。**書き込み可能な `SAKURA_PROXY_*` とは別物**（要件4）。
    URL_ENV = "REC_READONLY_PROXY_URL"
    KEY_ENV = "REC_READONLY_PROXY_KEY"

    # サーバー側のタイムアウトが 30 秒。当日画面は 45 秒ごとに更新されるので、
    # 1テーブルで 45 秒を食い潰さないよう既定はそれより短く取る。
    DEFAULT_TIMEOUT_SEC = 20.0

    # DATETIME は UTC で入っている（AGENTS.md「タイムスタンプは UTC 保存」）。
    # DumpSource と同じ列を同じ形（tz-aware UTC）に揃える。
    _DATETIME_COLS = DumpSource._DATETIME_COLS

    def __init__(self, base_url: str | None = None, key: str | None = None, *,
                 timeout_sec: float | None = None) -> None:
        self.base_url = (base_url or os.environ.get(self.URL_ENV) or "").strip()
        self._key = (key or os.environ.get(self.KEY_ENV) or "").strip()
        self.timeout_sec = timeout_sec if timeout_sec is not None else self.DEFAULT_TIMEOUT_SEC
        missing = [n for n, v in ((self.URL_ENV, self.base_url), (self.KEY_ENV, self._key)) if not v]
        if missing:
            raise RuntimeError(
                f"読み取り専用プロキシの接続情報が無い: {', '.join(missing)} を設定すること"
                "（ADR 0001 案A′。書き込み可能な SAKURA_PROXY_* は使わない）"
            )

    # -- SQL の組み立て（テーブル名も列も定義から作る。外から文字列を受けない） --

    @staticmethod
    def columns_for(name: str) -> tuple[str, ...]:
        """`LIVE_TABLES` / `POST_TABLES` に定義された列。未知のテーブルは拒否する。"""
        cols = LIVE_TABLES.get(name) or POST_TABLES.get(name)
        if not cols:
            raise ValueError(
                f"未知のテーブル: {name!r}。LIVE_TABLES / POST_TABLES に定義されたものだけを読む"
            )
        _reject_forbidden(name, cols)
        return cols

    @classmethod
    def build_sql(cls, name: str) -> str:
        cols = cls.columns_for(name)
        sql = f"SELECT {', '.join(f'`{c}`' for c in cols)} FROM `{name}`"
        if not _is_select_only(sql):
            raise ValueError(f"SELECT 以外は送らない（組み立てに失敗した）: {name}")
        return sql

    # -- 送信 --

    def table(self, name: str) -> pd.DataFrame:
        sql = self.build_sql(name)
        rows = self._post(sql, name)
        return self._to_frame(name, rows)

    def _post(self, sql: str, name: str) -> list:
        body = json.dumps({"sql": sql, "params": []}).encode("utf-8")
        req = urllib.request.Request(
            self.base_url,
            data=body,
            headers={"Content-Type": "application/json", "X-Proxy-Key": self._key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:  # noqa: S310
                if resp.status != 200:
                    raise _proxy_error(name, f"HTTP {resp.status}")
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # プロキシは MySQL のエラーをすべて 500 に潰す（server ADR 0001）。
            # 中身は分からない前提で、何を試したかだけを残す。
            raise _proxy_error(name, f"HTTP {exc.code}") from None
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
            raise _proxy_error(name, type(exc).__name__) from None
        except ValueError:
            raise _proxy_error(name, "応答が JSON ではない") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
            raise _proxy_error(name, "応答に rows が無い")
        return payload["rows"]

    def _to_frame(self, name: str, rows: list) -> pd.DataFrame:
        cols = self.columns_for(name)
        df = pd.DataFrame(rows, columns=list(cols)) if rows else pd.DataFrame(columns=list(cols))
        # 行が dict でない/列が欠けている場合でも、定義した列だけの形に揃える
        df = df.reindex(columns=list(cols))
        for col in self._DATETIME_COLS & set(df.columns):
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
        _reject_forbidden(name, df.columns)
        return df


def _is_select_only(sql: str) -> bool:
    """SELECT 1文だけであること。権限の前に、クライアント側でも書き込みを拒む（要件3）。"""
    s = sql.strip().rstrip(";").strip()
    if not s.upper().startswith("SELECT"):
        return False
    return ";" not in s


def _proxy_error(name: str, detail: str) -> RuntimeError:
    """**SQL 本文と鍵は絶対に載せない。** 何を読もうとしたかだけを残す（要件5）。"""
    return RuntimeError(
        f"読み取り専用プロキシからの取得に失敗した（テーブル: {name} / {detail}）。"
        "プロキシは MySQL のエラーを 500 に潰すため原因は判別できない"
        f"（{SqlSource.URL_ENV} の到達性と口の権限を確認する）"
    )


def _reject_forbidden(name: str, columns) -> None:
    hit = sorted(c for c in columns if (name, c) in FORBIDDEN_COLUMNS)
    if hit:
        raise ValueError(f"{name} に取得禁止の列が含まれる: {hit}（02 §2）。取得側で除外すること")


def attach_card_owner(df: pd.DataFrame, cards: pd.DataFrame, *, card_col: str = "card_id") -> pd.DataFrame:
    """`card_id` しか持たないテーブルに `user_id` / `event_id` を付ける。

    `card_unlock_events` と `bingo_cells` は実スキーマ上 `user_id` を持たない。
    指標・画面はどれも `user_id` で集計するので、**この関数を通してから渡す**。
    """
    if df.empty or card_col not in df.columns:
        return df
    keys = [c for c in ("id", "user_id", "event_id") if c in cards.columns]
    lookup = cards[keys].rename(columns={"id": card_col})
    return df.merge(lookup, on=card_col, how="left")


def attach_scores_event_id(
    df: pd.DataFrame, unlocks: pd.DataFrame, cards: pd.DataFrame,
    *, key_col: str = "unlock_event_id",
) -> pd.DataFrame:
    """`recommendation_scores` に **`event_id` だけ**を付ける。

    解決経路: `unlock_event_id` → `card_unlock_events.id` → `card_id`
    → `bingo_cards.event_id`。

    `attach_card_owner()` を流用しない: あれは `user_id` もマージするが、
    `recommendation_scores` は**すでに `user_id` を持つ**ため素直に merge すると
    `user_id_x` / `user_id_y` に割れて後続の `participants_only()` が壊れる。

    **`event_id` を既に持つ入力には何もしない。** 同じ理由で merge すると
    `event_id_x` / `event_id_y` に割れ、`scope_to_event()` が「`event_id` 列が無い表」
    として素通しする（=イベントで絞られない、という issue #14 の失敗がそのまま戻る）。
    `SqlSource` は `LIVE_TABLES` の列しか SELECT しないので起きないが、
    ダンプ／合成データは CSV にある列をそのまま読む。

    解決できない行（`unlock_event_id` が `card_unlock_events` に無い）は
    `event_id` が NaN になり、**絞り込み時に除外される**。`card_unlock_events` の
    `attach_card_owner()` 経路と同じ扱い（05 §3）。

    空 DataFrame・列欠けでは何もせず返す（当日監視の描画を落とさない。02 §4）。
    """
    if df.empty or key_col not in df.columns:
        return df
    if "event_id" in df.columns:
        return df
    if not {"id", "card_id"} <= set(unlocks.columns):
        return df
    if not {"id", "event_id"} <= set(cards.columns):
        return df
    card_of_unlock = unlocks[["id", "card_id"]].rename(columns={"id": key_col})
    event_of_card = cards[["id", "event_id"]].rename(columns={"id": "card_id"})
    resolved = card_of_unlock.merge(event_of_card, on="card_id", how="left")[[key_col, "event_id"]]
    return df.merge(resolved, on=key_col, how="left")


def scope_to_event(tables: dict[str, pd.DataFrame], event_id: str | None) -> dict[str, pd.DataFrame]:
    """1イベントぶんに絞る。**絞り込み規則はここだけに書く**（ADR 0001）。

    `recommendation_scores` / `card_unlock_events` に `event_id` 列は無いため、
    `card_unlock_events` → `bingo_cards` の JOIN で得た `event_id` を使う
    （`card_unlock_events` は `attach_card_owner()`、`recommendation_scores` は
    `attach_scores_event_id()`。どちらも `load_tables()` が適用済みで渡す）。

    **`users.event_id` では絞らない。** 出展者・運営アカウントが混ざるため
    （event-support-server `docs/reference/api-endpoints.md`）。
    """
    if event_id is None:
        return tables
    out = {}
    for name, df in tables.items():
        if name == "users" or df.empty or "event_id" not in df.columns:
            out[name] = df
        else:
            out[name] = df[df["event_id"] == event_id].copy()
    return out


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


def load_tables(source: Source, names: tuple[str, ...] | None = None, *,
                event_id: str | None = None, exclude_staff: bool = True) -> dict[str, pd.DataFrame]:
    """指標・画面が使う形までまとめて整えて返す。**取得の作法はここに集約する。**

    1. 必要なテーブルを読む
    2. `card_id` しか持たないテーブルに `user_id` / `event_id` を付ける
    3. イベントで絞る（指定時）
    4. `role <> 'participant'` を除外する

    画面側はこれを呼ぶだけでよい。取得口（ダンプ／合成／プロキシ）は問わない。
    """
    names = names or tuple(LIVE_TABLES)
    want = set(names)
    need = set(want)
    if want & set(CARD_KEYED_TABLES):
        need |= {"bingo_cards"}
    if "recommendation_scores" in want:
        # scores は card_id を持たない。unlock_event_id → card_unlock_events →
        # bingo_cards で event_id を解決するため、names に無くても両方を取る。
        need |= {"card_unlock_events", "bingo_cards"}
    need |= {"users"} if exclude_staff else set()

    tables = {n: source.table(n) for n in need}

    cards = tables.get("bingo_cards")
    if cards is not None:
        for name in CARD_KEYED_TABLES:
            if name in tables:
                tables[name] = attach_card_owner(tables[name], cards)
        if "recommendation_scores" in tables and "card_unlock_events" in tables:
            tables["recommendation_scores"] = attach_scores_event_id(
                tables["recommendation_scores"], tables["card_unlock_events"], cards)

    tables = scope_to_event(tables, event_id)

    if exclude_staff:
        users = tables["users"]
        for name, df in tables.items():
            if name != "users" and "user_id" in df.columns:
                tables[name] = participants_only(df, users)

    return {n: tables[n] for n in names}


#: `/ops/state` の取得結果の区別（03「/ops/state が取れないとき」）。
#: **「トークンの設定漏れ」と「推薦エンジンが落ちている」を画面上で区別するため**にある。
OPS_OK = "ok"
OPS_AUTH_ERROR = "auth"          # 401 / 403。こちらの設定の問題
OPS_UNAVAILABLE = "unavailable"  # 接続失敗・タイムアウト・404・5xx。向こうの問題


class OpsStateResult(NamedTuple):
    """`OpsStateClient.fetch_result()` の戻り。**例外は投げない**ので、失敗はここに載る。"""

    state: dict | None
    status: str


class OpsStateClient:
    """推薦エンジンの `/ops/state`。**取得できなくても画面を止めない**（02 §1）。

    本番の `/ops/*` は `OPS_TOKEN` による認証が必須である（推薦側 ADR 0008）。
    **未設定なら 404、設定済みでトークン無しなら 401** になるため、
    トークンを送らないと画面が恒久的に「取得不能」になる。

    ヘッダは **`X-Ops-Token` を使う。`Authorization` は使わない。**
    Cloud Run のプラットフォーム認証（IAM の ID トークン）が `Authorization` を使うので、
    将来 `/ops/*` を IAM で絞るときに層が衝突する（推薦側 ADR 0008 Q-1）。
    """

    #: 推薦サービスの `OPS_TOKEN` と**同一の秘密**。Secret Manager の同じシークレットを渡す。
    TOKEN_ENV = "RECOMMEND_OPS_TOKEN"

    #: 認証ヘッダ。**`Authorization` に変えない**（上記の理由）。
    TOKEN_HEADER = "X-Ops-Token"

    def __init__(self, base_url: str | None, timeout_sec: float = 3.0,
                 token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.timeout_sec = timeout_sec
        self._token = (token if token is not None else os.environ.get(self.TOKEN_ENV, "")).strip()

    def fetch(self) -> dict | None:
        """成功時 dict、失敗時 None。**どんな失敗でも例外を投げない。**"""
        return self.fetch_result().state

    def fetch_result(self) -> OpsStateResult:
        """取得結果を失敗の区別つきで返す。**どんな失敗でも例外を投げない。**

        推薦エンジンが死にかけのとき（再起動中・OOM 直前）は、接続不能だけでなく
        不正な HTTP レスポンス（`http.client.BadStatusLine` など）も返しうる。
        エンジンが最も怪しいまさにその瞬間に監視が消えては意味がないので、
        Exception まで広く捕まえる（02 §1）。

        401 / 403 だけは `OPS_AUTH_ERROR` として区別する。**こちらの設定漏れと
        推薦エンジンの停止が画面上で同じ「取得不能」に見えると当日切り分けられない。**
        """
        if not self.base_url:
            return OpsStateResult(None, OPS_UNAVAILABLE)
        url = f"{self.base_url}/ops/state"
        # トークンは**ヘッダにだけ載せる**。URL のクエリにも画面にも出さない。
        headers = {self.TOKEN_HEADER: self._token} if self._token else {}
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:  # noqa: S310
                if resp.status != 200:
                    return OpsStateResult(None, OPS_UNAVAILABLE)
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 401: トークン未設定・誤り。403: 拒否。どちらもこちら側で直せる。
            # 404 は「OPS_TOKEN 未設定でルート自体が無い」＝向こうの設定なので unavailable。
            status = OPS_AUTH_ERROR if exc.code in (401, 403) else OPS_UNAVAILABLE
            return OpsStateResult(None, status)
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ValueError, OSError):
            return OpsStateResult(None, OPS_UNAVAILABLE)
        except Exception:  # noqa: BLE001 - 監視を落とさないことを最優先する
            return OpsStateResult(None, OPS_UNAVAILABLE)
        if not isinstance(payload, dict):
            return OpsStateResult(None, OPS_UNAVAILABLE)
        return OpsStateResult(payload, OPS_OK)
