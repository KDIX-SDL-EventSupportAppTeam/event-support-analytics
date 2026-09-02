"""読み取り専用の口を、**さくらへ渡す前に**ローカルで通しきる。

    python deploy/sakura-readonly-proxy/verify/verify.py

やること（本番のさくらには一切触らない）:

1. 使い捨ての MySQL 8 と PHP 8 を Docker で立てる
2. `event-support-server/db/create-tables.sql`（スキーマの正本）をそのまま流す
3. `grant.sql` の読み取り専用ユーザーを、本番と同じ文で作る
4. `index.php` を PHP で動かし、**`rec_db.SqlSource` から実際に読む**
5. 書き込みが二重に止まることを確かめる
   - PHP の SELECT 判定（403）
   - MySQL の権限（判定をすり抜けても書けない）

**すべて緑になったものだけを先生に渡す。** 先生の側での試行錯誤を無くすのが目的。

前提: Docker が動くこと。`../event-support-server` が手元にあること。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SERVER_REPO = REPO.parent / "event-support-server"
SCHEMA = SERVER_REPO / "db" / "create-tables.sql"

sys.path.insert(0, str(REPO / "src"))

DB = "event_support_verify"
ROOT_PW = "verifyroot"
RO_USER = "bingo_ro"
RO_PASS = "verify-ro-pass"
PROXY_URL = "http://127.0.0.1:18080/index.php"
PROXY_KEY = "verify-readonly-key"

_ok = 0
_ng = 0


def check(label: str, passed: bool, detail: str = "") -> None:
    global _ok, _ng
    if passed:
        _ok += 1
        print(f"  OK   {label}")
    else:
        _ng += 1
        print(f"  NG   {label}" + (f" … {detail}" if detail else ""))


def compose(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=HERE, text=True, **kw)


def mysql(sql: str, *, user: str = "root", password: str = ROOT_PW) -> subprocess.CompletedProcess:
    """MySQL クライアントをコンテナ内で叩く。戻り値の returncode で成否を見る。"""
    return compose(
        "exec", "-T", "mysql",
        "mysql", f"-u{user}", f"-p{password}", DB, "-e", sql,
        capture_output=True,
    )


def mysql_script(text: str) -> None:
    p = compose("exec", "-T", "mysql", "mysql", f"-uroot", f"-p{ROOT_PW}", DB,
                input=text, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"SQL の実行に失敗した: {p.stderr[-2000:]}")


def post(sql: str, params=None, key: str = PROXY_KEY) -> tuple[int, dict]:
    """口へ1リクエスト投げ、(HTTPステータス, JSON) を返す。"""
    req = urllib.request.Request(
        PROXY_URL,
        data=json.dumps({"sql": sql, "params": params or []}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Proxy-Key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, {"raw": body}


# --- 準備 --------------------------------------------------------------------

def wait_for_proxy(timeout_sec: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            post("SELECT 1 AS ok")
            return
        except OSError:
            time.sleep(1)
    raise RuntimeError("PHP が起動しない")


def load_schema() -> None:
    """スキーマの正本をそのまま流す。**ここで写経しない**（写経するとずれる）。"""
    sql = SCHEMA.read_text(encoding="utf-8")
    sql = sql.replace("USE `your_database_name`;", f"USE `{DB}`;")
    mysql_script(sql)


def seed() -> None:
    """指標が触る最小限の行。列は create-tables.sql の定義に従う。"""
    mysql_script(f"""
    SET time_zone = '+00:00';
    INSERT INTO organizers (id, email, password_hash, display_name)
      VALUES ('org1', 'org@example.com', 'x', '検証');
    INSERT INTO events (id, organizer_id, name, date_start, date_end)
      VALUES ('ev1', 'org1', '検証イベント', '2026-10-10 00:00:00', '2026-10-10 23:59:59'),
             ('ev2', 'org1', '別イベント',   '2026-11-11 00:00:00', '2026-11-11 23:59:59');
    INSERT INTO users (id, event_id, email, password_hash, role)
      VALUES ('u1', 'ev1', 'u1@example.com', 'hash1', 'participant'),
             ('s1', 'ev1', 's1@example.com', 'hash2', 'staff'),
             ('u2', 'ev2', 'u2@example.com', 'hash3', 'participant');
    INSERT INTO bingo_cards (id, event_id, user_id)
      VALUES ('c1', 'ev1', 'u1'), ('c2', 'ev1', 's1'), ('c3', 'ev2', 'u2');
    -- pair_key / line_index / released_positions は指標では使わないが NOT NULL なので埋める
    INSERT INTO card_unlock_events
      (id, card_id, pair_key, line_index, released_positions,
       phase, strategy, decision_table_size, global_checkin_count, created_at)
      VALUES ('e1', 'c1', 'A', 0, '0,1', 'EARLY', 'RECOMMEND', 10, 100, '2026-10-10 01:23:45'),
             ('e2', 'c2', 'A', 0, '0,1', 'EARLY', 'FALLBACK_COVERAGE', 10, 101, '2026-10-10 01:24:45'),
             ('e3', 'c3', 'A', 0, '0,1', 'LATE',  'RECOMMEND', 12, 102, '2026-11-11 01:25:45');
    """)


def main() -> int:
    if not SCHEMA.exists():
        print(f"スキーマの正本が無い: {SCHEMA}\n"
              "`../event-support-server` を手元に置いてから実行すること。")
        return 2

    print("== 使い捨ての MySQL と PHP を立てる（初回はイメージのビルドで数分）")
    compose("up", "-d", "--build", check=True)
    try:
        print("== スキーマ（create-tables.sql）を流す")
        load_schema()
        seed()

        print("== grant.sql の読み取り専用ユーザーを、本番と同じ文で作る")
        grant = (HERE.parent / "grant.sql").read_text(encoding="utf-8")
        grant = grant.replace("<DB名>", DB).replace("<パスワード>", RO_PASS)
        grant = grant.replace("'bingo_ro'@'localhost'", f"'{RO_USER}'@'%'")
        mysql_script(grant)

        wait_for_proxy()

        print("\n-- 1. 契約どおりに読める --------------------------------------")
        status, body = post("SELECT `id`, `role` FROM `users`")
        check("200 で rows が返る", status == 200 and isinstance(body.get("rows"), list), str(body))
        check("affectedRows は 0 / insertId は null",
              body.get("affectedRows") == 0 and body.get("insertId") is None)
        check("プレースホルダが効く",
              post("SELECT `id` FROM `users` WHERE `id` = ?", ["u1"])[1]["rows"] == [{"id": "u1"}])

        print("\n-- 2. 認証 ----------------------------------------------------")
        check("鍵が違えば 401", post("SELECT 1", key="wrong")[0] == 401)
        check("鍵が無ければ 401", post("SELECT 1", key="")[0] == 401)
        check("GET は 404", _get_status(PROXY_URL) == 404)

        print("\n-- 3. 書き込みは PHP が止める（1枚目の壁）---------------------")
        for sql in ("UPDATE users SET role = 'staff'",
                    "DELETE FROM check_ins",
                    "INSERT INTO users (id) VALUES ('x')",
                    "DROP TABLE users",
                    "SELECT 1; DROP TABLE users",
                    "/* c */ UPDATE users SET role = 'x'",
                    "-- c\nUPDATE users SET role = 'x'"):
            status, _ = post(sql)
            check(f"403 で拒む: {sql.splitlines()[0][:40]}", status == 403)

        print("\n-- 4. 書き込みは権限も止める（2枚目の壁・PHP をすり抜けた場合）")
        p = mysql("UPDATE users SET role = 'staff'", user=RO_USER, password=RO_PASS)
        check("読み取り専用ユーザーは UPDATE できない", p.returncode != 0, p.stdout)
        p = mysql("DELETE FROM check_ins", user=RO_USER, password=RO_PASS)
        check("読み取り専用ユーザーは DELETE できない", p.returncode != 0, p.stdout)
        p = mysql("SELECT email FROM users", user=RO_USER, password=RO_PASS)
        check("users.email は権限の側で読めない", p.returncode != 0, p.stdout)
        p = mysql("SELECT id, role FROM users", user=RO_USER, password=RO_PASS)
        check("users.id / role は読める", p.returncode == 0, p.stderr)

        print("\n-- 5. SqlSource から実際に読む --------------------------------")
        import rec_db  # noqa: PLC0415 - Docker を立ててから読み込む

        src = rec_db.SqlSource(PROXY_URL, PROXY_KEY)
        ue = src.table("card_unlock_events")
        check("列は LIVE_TABLES の定義どおり",
              list(ue.columns) == list(rec_db.LIVE_TABLES["card_unlock_events"]), str(list(ue.columns)))
        check("3行読めている", len(ue) == 3, str(len(ue)))
        check("created_at が UTC の datetime になっている",
              str(ue["created_at"].dt.tz) == "UTC" and str(ue["created_at"].iloc[0]).startswith(
                  "2026-10-10 01:23:45"), str(ue["created_at"].iloc[0]))

        # **全テーブルを1枚ずつ通す。** 1枚だけ試すと、列名が PHP の禁止語に
        # 引っかかる事故を見逃す（created_at の中の create で実際に踏んだ）。
        for name in (*rec_db.LIVE_TABLES, *rec_db.POST_TABLES):
            status, body = post(rec_db.SqlSource.build_sql(name))
            check(f"{name} を読める", status == 200, f"HTTP {status} {body}")

        tables = rec_db.load_tables(src, ("card_unlock_events",), event_id="ev1")
        got = tables["card_unlock_events"]
        check("load_tables が card_id → user_id を解決している",
              "user_id" in got.columns and list(got["user_id"]) == ["u1"], str(got.to_dict("records")))
        check("別イベントとスタッフが落ちている", len(got) == 1, str(len(got)))

        print(f"\n==== OK {_ok} / NG {_ng} ====")
        if _ng == 0:
            print("すべて通った。この構成のまま先生に渡してよい（README.md 参照）。")
        return 1 if _ng else 0
    finally:
        print("\n== 後片付け")
        compose("down", "-v", capture_output=True)


def _get_status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
