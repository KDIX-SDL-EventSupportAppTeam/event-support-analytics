import sys
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from build_tables import build_booths, build_participants, build_visits, write_csv  # noqa: E402
from test_dashboard import make_raw  # noqa: E402

APP = str(SRC / "dashboard.py")


@pytest.fixture
def tables(tmp_path, monkeypatch):
    # load() のキャッシュキーは相対パス "data/tables" 固定のため、
    # 前のテストが読んだ表が残る。テストごとに捨てる
    st.cache_data.clear()
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


def _text(at) -> str:
    return "\n".join(
        [e.value for e in at.error] + [m.value for m in at.markdown] + [c.value for c in at.code]
    )


def test_broken_data_shows_report_instead_of_crashing(tables):
    """列が壊れたCSVでも、赤いトレースバックではなくレポート画面が出る。"""
    (tables / "participants.csv").write_text("pid,whatever\nu0001,1\n", encoding="utf-8")

    at = AppTest.from_file(APP, default_timeout=60).run()

    assert not at.exception, "利用者に生の例外画面を見せてはいけない"
    body = _text(at)
    assert "画面を表示できませんでした" in body
    assert "レポート番号" in body
    assert "不具合レポート" in at.code[0].value
    assert "ValueError" in at.code[0].value, "担当者が原因を特定できる詳細が入っている"


def test_missing_tables_shows_data_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    body = _text(at)
    assert "表示するデータがありません" in body
    assert "中間テーブルがありません" in body
    assert "不具合レポート" in at.code[0].value


def test_report_contains_no_participant_data(tables):
    """レポートに行動データを混ぜない（転送されるため）。"""
    (tables / "participants.csv").write_text("pid,whatever\nu0001,1\n", encoding="utf-8")
    at = AppTest.from_file(APP, default_timeout=60).run()
    text = at.code[0].value
    assert "u0001" not in text


def test_normal_run_shows_no_error_screen(tables):
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert "画面を表示できませんでした" not in _text(at)
    assert at.metric
