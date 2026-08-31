import http.client
import json
import sys
import traceback
import urllib.error
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import rec_db  # noqa: E402
import synth_rec_data as synth  # noqa: E402


@pytest.fixture()
def dump_dir(tmp_path):
    synth.write(tmp_path, recommender_dead=False, with_ops_state=True, split_started=True)
    return tmp_path


def test_dump_source_reads_synth_tables(dump_dir):
    src = rec_db.SynthSource(dump_dir)
    ue = src.table("card_unlock_events")
    assert "strategy" in ue.columns
    assert pd.api.types.is_datetime64_any_dtype(ue["created_at"])


def test_forbidden_columns_are_rejected(tmp_path):
    pd.DataFrame({"id": ["u1"], "role": ["participant"], "email": ["x@example.com"]}).to_csv(
        tmp_path / "users.csv", index=False)
    with pytest.raises(ValueError, match="取得禁止"):
        rec_db.DumpSource(tmp_path).table("users")


# --- SqlSource（読み取り専用プロキシ。ADR 0001 案A′） -------------------------
#
# **実 DB にもネットワークにも接続しない。** urlopen をモックして契約だけを検証する。

_URL = "https://proxy.invalid/readonly"
_KEY = "s3cret-readonly-key"


class _FakeResponse:
    """さくらプロキシの 200 応答（{"rows": [...], ...}）。"""

    status = 200

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture()
def sql_source(monkeypatch):
    monkeypatch.setenv(rec_db.SqlSource.URL_ENV, _URL)
    monkeypatch.setenv(rec_db.SqlSource.KEY_ENV, _KEY)
    return rec_db.SqlSource()


def _stub_proxy(monkeypatch, rows_by_table, sent=None):
    """SQL からテーブル名を見て rows を返すだけの偽プロキシ。"""
    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        if sent is not None:
            sent.append({"url": req.full_url, "headers": dict(req.headers), "body": body})
        for name in rows_by_table:
            if f"FROM `{name}`" in body["sql"]:
                return _FakeResponse(
                    {"rows": rows_by_table[name], "affectedRows": 0, "insertId": None})
        raise AssertionError("想定外の SQL が送られた")

    monkeypatch.setattr(rec_db.urllib.request, "urlopen", fake_urlopen)


def test_sql_source_requires_readonly_env(monkeypatch):
    """書き込み可能な SAKURA_PROXY_* では動かない。読み取り専用の口の変数だけを見る（要件4）。"""
    monkeypatch.delenv(rec_db.SqlSource.URL_ENV, raising=False)
    monkeypatch.delenv(rec_db.SqlSource.KEY_ENV, raising=False)
    monkeypatch.setenv("SAKURA_PROXY_URL", "https://write.invalid/")
    monkeypatch.setenv("SAKURA_PROXY_KEY", "全権の鍵")
    with pytest.raises(RuntimeError, match=rec_db.SqlSource.URL_ENV):
        rec_db.SqlSource()


@pytest.mark.parametrize("name", sorted(rec_db.LIVE_TABLES))
def test_sql_source_selects_only_declared_columns(name):
    """`SELECT *` を書かない。列は LIVE_TABLES の定義そのもの（02 §2）。"""
    sql = rec_db.SqlSource.build_sql(name)
    assert "*" not in sql
    assert sql.startswith("SELECT ")
    assert sql.endswith(f"FROM `{name}`")
    selected = [c.strip("`") for c in sql[len("SELECT "):sql.index(" FROM")].split(", ")]
    assert selected == list(rec_db.LIVE_TABLES[name])


def test_sql_source_never_selects_forbidden_columns():
    """users.email / password_hash は構造的に要求できない（02 §2）。"""
    sql = rec_db.SqlSource.build_sql("users")
    for _, col in rec_db.FORBIDDEN_COLUMNS:
        assert col not in sql


@pytest.mark.parametrize("name", ["audit_logs", "users; DROP TABLE users", "", "USERS"])
def test_sql_source_rejects_unknown_tables(name):
    with pytest.raises(ValueError, match="未知のテーブル"):
        rec_db.SqlSource.build_sql(name)


@pytest.mark.parametrize("sql", [
    "UPDATE `users` SET role = 'x'",
    "DELETE FROM `check_ins`",
    "SELECT `id` FROM `users`; DROP TABLE users",
    "  insert into users values (1)",
])
def test_sql_source_rejects_non_select(sql):
    """権限が最終的な保証だが、クライアント側でも SELECT 以外を通さない（要件3）。"""
    assert not rec_db._is_select_only(sql)


def test_sql_source_posts_the_proxy_contract(monkeypatch, sql_source):
    """POST <base_url> / X-Proxy-Key / {"sql", "params"}（http-proxy.ts の契約）。"""
    sent = []
    _stub_proxy(monkeypatch, {"users": [{"id": "u1", "role": "participant"}]}, sent)
    sql_source.table("users")
    assert len(sent) == 1
    req = sent[0]
    assert req["url"] == _URL
    assert req["headers"]["X-proxy-key"] == _KEY  # urllib はヘッダ名を capitalize する
    assert req["body"] == {"sql": rec_db.SqlSource.build_sql("users"), "params": []}


@pytest.mark.parametrize("boom", [
    urllib.error.HTTPError(_URL, 500, "Internal Server Error", {}, None),
    urllib.error.URLError("接続できない"),
    TimeoutError("timeout"),
])
def test_sql_source_error_names_the_table_and_hides_the_key(monkeypatch, sql_source, boom):
    """500 に潰れる前提で「何を試したか」だけを残す。**鍵と SQL 本文は出さない**（要件5）。"""
    def fake_urlopen(*_a, **_k):
        raise boom

    monkeypatch.setattr(rec_db.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError) as excinfo:
        sql_source.table("check_ins")
    message = str(excinfo.value)
    assert "check_ins" in message
    assert _KEY not in message
    assert "SELECT" not in message


def test_sql_source_error_chain_never_leaks_the_key(monkeypatch, sql_source):
    """例外連鎖（traceback）にも鍵が残らないこと。"""
    def fake_urlopen(*_a, **_k):
        raise urllib.error.URLError(f"failed to connect with key {_KEY}")

    monkeypatch.setattr(rec_db.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError) as excinfo:
        sql_source.table("users")
    assert excinfo.value.__cause__ is None
    assert _KEY not in "".join(traceback.format_exception(excinfo.value))


def test_sql_source_frame_matches_dump_source_shape(monkeypatch, sql_source, dump_dir):
    """返った rows が DumpSource と同じ形（列・日時は UTC の datetime）になる。"""
    rows = [{"id": "e1", "card_id": "c1", "phase": 1, "strategy": "RECOMMEND",
             "decision_table_size": 10, "global_checkin_count": 100,
             "created_at": "2026-10-10 01:23:45"}]
    _stub_proxy(monkeypatch, {"card_unlock_events": rows})
    got = sql_source.table("card_unlock_events")
    expected = rec_db.SynthSource(dump_dir).table("card_unlock_events")

    assert list(got.columns) == list(rec_db.LIVE_TABLES["card_unlock_events"])
    assert set(got.columns) <= set(expected.columns)
    assert pd.api.types.is_datetime64_any_dtype(got["created_at"])
    assert str(got["created_at"].dt.tz) == "UTC"


def test_sql_source_empty_rows_keep_the_columns(monkeypatch, sql_source):
    """0件でも列は保つ。当日の開始直後に画面が KeyError で落ちないため。"""
    _stub_proxy(monkeypatch, {"booth_ratings": []})
    got = sql_source.table("booth_ratings")
    assert got.empty
    assert list(got.columns) == list(rec_db.LIVE_TABLES["booth_ratings"])


def test_sql_source_rejects_malformed_payload(monkeypatch, sql_source):
    monkeypatch.setattr(rec_db.urllib.request, "urlopen",
                        lambda *_a, **_k: _FakeResponse({"affectedRows": 0}))
    with pytest.raises(RuntimeError, match="users"):
        sql_source.table("users")


def test_sql_source_works_through_load_tables(monkeypatch, sql_source):
    """load_tables に渡して card_id → user_id の解決まで通ること（ADR 0001「影響」）。"""
    _stub_proxy(monkeypatch, {
        "bingo_cards": [{"id": "c1", "event_id": "ev1", "user_id": "u1"},
                        {"id": "c2", "event_id": "ev1", "user_id": "s1"},
                        {"id": "c3", "event_id": "ev2", "user_id": "u2"}],
        "card_unlock_events": [
            {"id": "e1", "card_id": "c1", "phase": 1, "strategy": "RECOMMEND",
             "decision_table_size": 10, "global_checkin_count": 100,
             "created_at": "2026-10-10 01:23:45"},
            {"id": "e2", "card_id": "c2", "phase": 1, "strategy": "FALLBACK_COVERAGE",
             "decision_table_size": 10, "global_checkin_count": 101,
             "created_at": "2026-10-10 01:24:45"},
            {"id": "e3", "card_id": "c3", "phase": 2, "strategy": "RECOMMEND",
             "decision_table_size": 12, "global_checkin_count": 102,
             "created_at": "2026-10-10 01:25:45"},
        ],
        "users": [{"id": "u1", "role": "participant"}, {"id": "u2", "role": "participant"},
                  {"id": "s1", "role": "staff"}],
    })
    tables = rec_db.load_tables(sql_source, ("card_unlock_events",), event_id="ev1")
    ue = tables["card_unlock_events"]
    assert set(ue.columns) >= {"user_id", "event_id"}  # card_id から解決されている
    assert list(ue["user_id"]) == ["u1"]  # 別イベント(ev2)とスタッフ(s1)は落ちている


def test_participants_only_excludes_staff():
    users = pd.DataFrame({"id": ["u1", "u2", "s1"], "role": ["participant", "participant", "staff"]})
    df = pd.DataFrame({"user_id": ["u1", "u2", "s1"], "x": [1, 2, 3]})
    kept = rec_db.participants_only(df, users)
    assert set(kept["user_id"]) == {"u1", "u2"}


def test_ops_state_client_returns_none_without_url():
    assert rec_db.OpsStateClient(None).fetch() is None


def test_ops_state_client_swallows_connection_errors():
    # 到達不能ポート。例外を投げず None を返す（02 §1）
    assert rec_db.OpsStateClient("http://127.0.0.1:1").fetch() is None


@pytest.mark.parametrize("exc", [
    http.client.BadStatusLine("garbage"),
    http.client.IncompleteRead(b""),
    RuntimeError("想定外"),
])
def test_ops_state_client_swallows_malformed_http_response(monkeypatch, exc):
    """エンジンが死にかけで不正な HTTP レスポンスを返しても監視を落とさない（02 §1）。"""
    def boom(*_a, **_k):
        raise exc

    monkeypatch.setattr(rec_db.urllib.request, "urlopen", boom)
    assert rec_db.OpsStateClient("http://example.invalid").fetch() is None


def test_ops_state_client_rejects_non_dict_payload(monkeypatch):
    class _Resp:
        status = 200

        def read(self):
            return b"[1, 2, 3]"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(rec_db.urllib.request, "urlopen", lambda *_a, **_k: _Resp())
    assert rec_db.OpsStateClient("http://example.invalid").fetch() is None
