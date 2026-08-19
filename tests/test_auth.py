import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from build_tables import build_booths, build_participants, build_visits, write_csv  # noqa: E402
from test_dashboard import make_raw  # noqa: E402

APP = str(SRC / "dashboard.py")


@pytest.fixture
def tables(tmp_path, monkeypatch):
    raw = make_raw()
    booths = build_booths(raw)
    visits, _ = build_visits(raw, booths)
    participants = build_participants(raw, visits)
    tables_dir = tmp_path / "data" / "tables"
    write_csv(visits, tables_dir / "visits.csv")
    write_csv(participants, tables_dir / "participants.csv")
    write_csv(booths, tables_dir / "booths.csv")
    monkeypatch.chdir(tmp_path)
    return tables_dir


def test_no_password_configured_means_no_gate(tables, monkeypatch):
    """ローカル実行（合言葉未設定）は素通りする。"""
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.delenv("DASHBOARD_AUTH_REQUIRED", raising=False)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert at.metric  # 本体が描画されている


def test_password_gate_blocks_until_correct_answer(tables, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "protofes-2026")
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert not at.metric, "合言葉を入れる前に中身が見えてはいけない"

    at.text_input[0].set_value("ちがう合言葉").run()
    at.button[0].click().run()
    assert not at.metric
    assert any("合言葉が違います" in e.value for e in at.error)

    at.text_input[0].set_value("protofes-2026").run()
    at.button[0].click().run()
    assert not at.exception
    assert at.metric, "正しい合言葉なら中身が見える"


def test_container_without_secret_refuses_to_serve(tables, monkeypatch):
    """設定漏れで全世界に公開されないこと。"""
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.setenv("DASHBOARD_AUTH_REQUIRED", "1")
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.metric
    assert any("合言葉が設定されていません" in e.value for e in at.error)


def test_trailing_newline_in_secret_is_tolerated(tables, monkeypatch):
    """シークレット登録時に混入しがちな改行で締め出されないこと。"""
    monkeypatch.setenv("DASHBOARD_PASSWORD", "protofes-2026\n")
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_input[0].set_value("protofes-2026").run()
    at.button[0].click().run()
    assert not at.exception
    assert at.metric
