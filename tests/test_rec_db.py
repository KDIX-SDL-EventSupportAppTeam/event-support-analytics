import http.client
import sys
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


def test_sql_source_not_implemented_points_to_spec():
    with pytest.raises(NotImplementedError, match="E-1"):
        rec_db.SqlSource()


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
