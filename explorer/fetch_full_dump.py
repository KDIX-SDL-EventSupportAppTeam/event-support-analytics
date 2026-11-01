"""本番 DB（読み取り専用プロキシ）からイベントの全テーブルをローカルにダンプする。

使い方（event-support-analytics で）:
    .venv/bin/python explorer/fetch_full_dump.py <出力ディレクトリ> <event_id>
    .venv/bin/python explorer/fetch_full_dump.py <出力ディレクトリ> <event_id> --with-email

--with-email: メール列を持つテーブル（users / organizers / exhibitor_invites）を、メール列込みの完全な行で
  <出力>/sensitive/<table>.parquet に別保存する。tables/ 側（explorer が読む方）にはメールを入れない。
  パスワード・トークン・secret の列は、このモードでも取らない。

方針:
- **行の中身は標準出力に一切出さない。** 出すのはテーブル名・行数・失敗したテーブル名だけ
- 列は information_schema から取り、DB と同じテーブル単位（正規化されたまま）で 1 テーブル 1 ファイル
- 値は加工しない（全列を文字列として保存。NULL は null のまま。時刻も DB の保存値のまま）
- 認証情報に当たる列（パスワードハッシュ・トークン・メール）は取らない
- 出力は Parquet（列指向で検索・絞り込みが速い）＋ manifest.json（行数・列・ハッシュ・取得条件）
- 出力ディレクトリは 700、ファイルは 600

接続情報は .env の REC_READONLY_PROXY_URL / REC_READONLY_PROXY_KEY（書き込み可能な SAKURA_PROXY_* は使わない）。
"""

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

PAGE = 5000
TIMEOUT = 25
# 認証情報。分析に不要で、漏れたときの被害が大きいので構造的に取らない
SKIP_TABLES = {"email_verification_tokens", "password_reset_tokens"}
SKIP_COLUMN = re.compile(r"(password|token|secret|email)", re.I)
NEVER_COLUMN = re.compile(r"(password|token|secret)", re.I)   # --with-email でも取らない
EMAIL_COLUMN = re.compile(r"email", re.I)
IDENT = re.compile(r"^[A-Za-z0-9_]+$")


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class Proxy:
    def __init__(self) -> None:
        self.url = os.environ.get("REC_READONLY_PROXY_URL", "").strip()
        self.key = os.environ.get("REC_READONLY_PROXY_KEY", "").strip()
        if not self.url or not self.key:
            sys.exit("REC_READONLY_PROXY_URL / REC_READONLY_PROXY_KEY が設定されていない")

    def query(self, sql: str, params: list, label: str) -> list:
        s = sql.strip()
        if not s.upper().startswith("SELECT") or ";" in s:
            raise ValueError(f"SELECT 以外は送らない: {label}")
        body = json.dumps({"sql": s, "params": params}).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "X-Proxy-Key": self.key})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{label}: HTTP {e.code}") from None
        except Exception as e:  # noqa: BLE001 — 本文や鍵を例外に載せない
            raise RuntimeError(f"{label}: {type(e).__name__}") from None
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError(f"{label}: 応答に rows が無い")
        return rows


def scope(table: str, cols: list[str], schema: dict[str, list[str]]) -> tuple[str, int]:
    """イベントに属する行だけに絞る WHERE と、event_id プレースホルダの数。"""
    if "event_id" in cols:
        return "`event_id` = ?", 1
    if table == "events":
        return "`id` = ?", 1
    if "card_id" in cols and "bingo_cards" in schema:
        return "`card_id` IN (SELECT `id` FROM `bingo_cards` WHERE `event_id` = ?)", 1
    if "booth_id" in cols and "booths" in schema:
        return "`booth_id` IN (SELECT `id` FROM `booths` WHERE `event_id` = ?)", 1
    if "user_id" in cols and "event_id" in schema.get("users", []):
        return "`user_id` IN (SELECT `id` FROM `users` WHERE `event_id` = ?)", 1
    return "", 0   # マスタ（categories など）は全件


def to_str(v):
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def fetch_rows(proxy, t, cols, where, n, order, event_id):
    rows, offset = [], 0
    sel = ", ".join(f"`{c}`" for c in cols)
    while True:
        sql = f"SELECT {sel} FROM `{t}`" + (f" WHERE {where}" if where else "") + f" ORDER BY {order} LIMIT {PAGE} OFFSET {offset}"
        page = proxy.query(sql, [event_id] * n, t)
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        offset += PAGE


def write_parquet(path: Path, cols, rows):
    data = {c: [to_str(r.get(c)) if isinstance(r, dict) else None for r in rows] for c in cols}
    pq.write_table(pa.table({c: pa.array(v, type=pa.string()) for c, v in data.items()}), path, compression="zstd")
    os.chmod(path, 0o600)


def main_email(out: Path, event_id: str) -> None:
    """メール列込みの行を sensitive/ に保存する。値は一切出力しない。"""
    load_env(Path(__file__).resolve().parent.parent / ".env")
    proxy = Proxy()
    mpath = out / "manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    meta_rows = proxy.query(
        "SELECT TABLE_NAME AS t, COLUMN_NAME AS c, DATA_TYPE AS d, COLUMN_KEY AS k FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION", [], "information_schema")
    schema, types, pks = {}, {}, {}
    for r in meta_rows:
        if IDENT.match(r["t"]) and IDENT.match(r["c"]):
            schema.setdefault(r["t"], []).append(r["c"]); types.setdefault(r["t"], {})[r["c"]] = r["d"]
            if r.get("k") == "PRI":
                pks.setdefault(r["t"], []).append(r["c"])
    sdir = out / "sensitive"
    sdir.mkdir(exist_ok=True); os.chmod(sdir, 0o700)
    manifest["sensitive"] = {}
    for t in sorted(schema):
        if t in SKIP_TABLES or not any(EMAIL_COLUMN.search(c) for c in schema[t]):
            continue
        cols = [c for c in schema[t] if not NEVER_COLUMN.search(c)]
        where, n = scope(t, schema[t], schema)
        order = ", ".join(f"`{c}`" for c in (pks.get(t) or cols[:1]))
        rows = fetch_rows(proxy, t, cols, where, n, order, event_id)
        path = sdir / f"{t}.parquet"
        write_parquet(path, cols, rows)
        base = manifest["tables"].get(t, {}).get("rows")
        manifest["sensitive"][t] = {
            "file": f"sensitive/{t}.parquet", "rows": len(rows), "primary_key": pks.get(t, []),
            "columns": [{"name": c, "db_type": types[t][c]} for c in cols],
            "email_columns": [c for c in cols if EMAIL_COLUMN.search(c)],
            "excluded_columns": [c for c in schema[t] if NEVER_COLUMN.search(c)],
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        print(f"  {t}: {len(rows)} 行（tables/ 側 {base} 行）{'' if base == len(rows) else ' ※件数が違う'}")
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(mpath, 0o600)


def main(out: Path, event_id: str) -> None:
    load_env(Path(__file__).resolve().parent.parent / ".env")
    proxy = Proxy()
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    (out / "tables").mkdir(exist_ok=True)
    os.chmod(out / "tables", 0o700)

    meta_rows = proxy.query(
        "SELECT TABLE_NAME AS t, COLUMN_NAME AS c, DATA_TYPE AS d, COLUMN_KEY AS k, ORDINAL_POSITION AS p "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION",
        [], "information_schema")
    schema: dict[str, list[str]] = {}
    types: dict[str, dict[str, str]] = {}
    pks: dict[str, list[str]] = {}
    for r in meta_rows:
        t, c = r["t"], r["c"]
        if not (IDENT.match(t) and IDENT.match(c)):
            continue
        schema.setdefault(t, []).append(c)
        types.setdefault(t, {})[c] = r["d"]
        if r.get("k") == "PRI":
            pks.setdefault(t, []).append(c)

    manifest = {"event_id": event_id, "fetched_at": datetime.now(timezone.utc).isoformat(),
                "format": "1 テーブル 1 Parquet。全列 string、NULL は null。値は DB の保存値のまま",
                "excluded_tables": sorted(SKIP_TABLES & set(schema)), "tables": {}, "failed": []}
    print(f"{len(schema)} テーブルを確認")

    for t in sorted(schema):
        if t in SKIP_TABLES:
            continue
        cols = [c for c in schema[t] if not SKIP_COLUMN.search(c)]
        skipped = [c for c in schema[t] if SKIP_COLUMN.search(c)]
        where, n = scope(t, schema[t], schema)
        order = ", ".join(f"`{c}`" for c in (pks.get(t) or cols[:1]))
        sel = ", ".join(f"`{c}`" for c in cols)
        rows: list = []
        try:
            offset = 0
            while True:
                sql = f"SELECT {sel} FROM `{t}`" + (f" WHERE {where}" if where else "") + f" ORDER BY {order} LIMIT {PAGE} OFFSET {offset}"
                page = proxy.query(sql, [event_id] * n, t)
                rows.extend(page)
                if len(page) < PAGE:
                    break
                offset += PAGE
        except RuntimeError as e:
            manifest["failed"].append(str(e))
            print(f"  失敗 {e}")
            continue
        data = {c: [to_str(r.get(c)) if isinstance(r, dict) else None for r in rows] for c in cols}
        table = pa.table({c: pa.array(v, type=pa.string()) for c, v in data.items()}) if cols else pa.table({})
        path = out / "tables" / f"{t}.parquet"
        pq.write_table(table, path, compression="zstd")
        os.chmod(path, 0o600)
        manifest["tables"][t] = {
            "file": f"tables/{t}.parquet", "rows": len(rows), "primary_key": pks.get(t, []),
            "columns": [{"name": c, "db_type": types[t][c]} for c in cols], "excluded_columns": skipped,
            "scope": where.replace("?", "<event_id>") or "全件", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        print(f"  {t}: {len(rows)} 行")

    mpath = out / "manifest.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(mpath, 0o600)
    print(f"完了: {len(manifest['tables'])} テーブル / 失敗 {len(manifest['failed'])}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        sys.exit(__doc__)
    (main_email if "--with-email" in sys.argv else main)(Path(args[0]), args[1])
