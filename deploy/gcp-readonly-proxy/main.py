"""読み取り専用プロキシ（GCP 版）— さくら側を触らずに「SELECT しか通らない口」を作る。

`deploy/sakura-readonly-proxy/`（さくらに PHP を置く案）の代替。さくらには何も置かず、
Cloud Run 上のこのサービスが既存の全権の口（/bingo/query/index.php）の前に立つ。

    推薦エンジン / 分析 ──(読み取り専用の鍵)──▶ このサービス ──(全権の鍵)──▶ さくら既存プロキシ ──▶ MySQL

契約は既存の口・sakura-readonly-proxy/index.php と同じ（event-support-server/src/db/http-proxy.ts が正本）:

    POST /   headers: X-Proxy-Key: <読み取り専用の鍵>   body: {"sql": "...", "params": [...]}
    → 200 {"rows": [...], "affectedRows": 0, "insertId": null}
    → 401 Unauthorized / 400 Bad Request / 403 Forbidden: read-only endpoint / 500 / 502

**さくら版は「MySQL の権限」と「SELECT 判定」の二重の壁だった。GCP 版では MySQL の権限を張れない**
（全権の鍵で上流を叩くため）。代わりに SQL を**文法のホワイトリスト**で縛り、grant.sql と同じ範囲
（許可テーブルと、users は id / role のみ）しか読めないようにする。
通すのは呼び出し側（recommend の live_tables.build_select / analytics の rec_db.build_sql）が
機械的に組み立てる次の形だけ:

    SELECT `c1`, `c2`, ... FROM `table` [WHERE `event_id` = ?]
    SELECT `c1`, ... FROM `bingo_cells` WHERE `card_id` IN (SELECT `id` FROM `bingo_cards` WHERE `event_id` = ?)

後者は event_id 列を持たない表を親の event_id で絞る形で、PARENT_SCOPE に書いた組み合わせだけ通す。
全権の鍵はこのサービスの中にだけ置き、呼び出し側には読み取り専用の鍵だけを配る。
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("readonly-proxy")

# grant.sql と同じ範囲。None は「全列可」、集合は「その列だけ可」
ALLOWED: dict[str, frozenset[str] | None] = {
    "bingo_cards": None,
    "bingo_cells": None,
    "card_unlock_events": None,
    "check_ins": None,
    "booth_ratings": None,
    "recommendation_scores": None,
    "user_survey_answers": None,
    "booths": None,
    "categories": None,
    "booth_tags": None,
    "users": frozenset({"id", "role"}),
}

# event_id 列を持たない表 → (結合列, 親の表)。この組み合わせのサブクエリだけ通す
PARENT_SCOPE: dict[str, tuple[str, str]] = {
    "bingo_cells": ("card_id", "bingo_cards"),
}

_IDENT = r"`([A-Za-z_][A-Za-z0-9_]*)`"
_SELECT_RE = re.compile(
    rf"^SELECT\s+(?P<cols>{_IDENT}(?:\s*,\s*{_IDENT})*)\s+FROM\s+`(?P<table>[A-Za-z_][A-Za-z0-9_]*)`"
    rf"(?:\s+WHERE\s+(?:`event_id`\s*=\s*\?"
    rf"|`(?P<fk>[A-Za-z_][A-Za-z0-9_]*)`\s+IN\s*\(\s*SELECT\s+`id`\s+FROM\s+"
    rf"`(?P<parent>[A-Za-z_][A-Za-z0-9_]*)`\s+WHERE\s+`event_id`\s*=\s*\?\s*\)))?$",
    re.IGNORECASE,
)
_COL_RE = re.compile(_IDENT)

MAX_BODY_BYTES = 64 * 1024


def check_sql(sql: str, params: list) -> str | None:
    """通してよければ None、拒むなら理由を返す。迷ったら拒む側に倒す。"""
    s = sql.strip().rstrip(";").strip()
    m = _SELECT_RE.match(s)
    if not m:
        return "shape"
    cols = _COL_RE.findall(m.group("cols"))
    table = m.group("table")
    if table not in ALLOWED:
        return "table"
    allowed_cols = ALLOWED[table]
    if allowed_cols is not None and not set(cols) <= allowed_cols:
        return "column"
    if m.group("fk") is not None and PARENT_SCOPE.get(table) != (m.group("fk"), m.group("parent")):
        return "scope"
    if len(params) != s.count("?"):
        return "params"
    if not all(isinstance(p, (str, int, float)) and not isinstance(p, bool) for p in params):
        return "params"
    return None


def forward(sql: str, params: list) -> dict:
    """さくら既存プロキシへ転送する。応答は rows だけ取り出し affectedRows は 0 に固定する。"""
    req = urllib.request.Request(
        os.environ["UPSTREAM_PROXY_URL"],
        method="POST",
        data=json.dumps({"sql": sql, "params": params}).encode(),
        headers={"Content-Type": "application/json", "X-Proxy-Key": os.environ["UPSTREAM_PROXY_KEY"]},
    )
    timeout = float(os.environ.get("UPSTREAM_TIMEOUT_SEC", "25"))
    # さくらは稀に 502 を返す。SELECT だけなので再送しても副作用は無い。5xx に限り1回だけ再試行する
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code < 500:
            raise
        log.warning("upstream status=%s retrying", e.code)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read())
    return {"rows": body.get("rows", []), "affectedRows": 0, "insertId": None}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            return self._send(200, {"ok": True})
        self._send(404, {"error": "Not Found"})

    def do_POST(self) -> None:  # noqa: N802
        key = self.headers.get("X-Proxy-Key", "")
        expected = os.environ.get("READONLY_PROXY_KEY", "")
        if not key or not expected or not hmac.compare_digest(key.encode(), expected.encode()):
            return self._send(401, {"error": "Unauthorized"})

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            return self._send(400, {"error": "Bad Request: sql(string) と params(array) が必要です"})
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("sql"), str):
            return self._send(400, {"error": "Bad Request: sql(string) と params(array) が必要です"})
        params = body.get("params", [])
        if not isinstance(params, list):
            return self._send(400, {"error": "Bad Request: sql(string) と params(array) が必要です"})

        reason = check_sql(body["sql"], params)
        if reason:
            # SQL 本文はログに残さない（列名・表名だけでも推測材料になるため理由だけ）
            log.warning("rejected reason=%s", reason)
            return self._send(403, {"error": "Forbidden: read-only endpoint"})

        try:
            return self._send(200, forward(body["sql"], params))
        except urllib.error.HTTPError as e:
            log.error("upstream status=%s", e.code)
            return self._send(502, {"error": "Bad Gateway"})
        except Exception as e:  # noqa: BLE001  上流の詳細は返さない（既存の口と同じく潰す）
            log.error("upstream error=%s", type(e).__name__)
            return self._send(502, {"error": "Bad Gateway"})

    def log_message(self, fmt: str, *args) -> None:  # アクセスログは Cloud Run が取る
        pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for name in ("READONLY_PROXY_KEY", "UPSTREAM_PROXY_URL", "UPSTREAM_PROXY_KEY"):
        if not os.environ.get(name):
            raise SystemExit(f"{name} が未設定")
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
