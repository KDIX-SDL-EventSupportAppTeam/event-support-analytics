import os
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from build_tables import build_booths, build_participants, build_visits, write_csv  # noqa: E402

APP = str(SRC / "dashboard.py")


def make_raw(n_users: int = 25):
    checkins, bingo_card, users = [], [], []
    for i in range(1, n_users + 1):
        pid = f"u{i:04d}"
        checkins.append({"pid": pid, "booth_id": "①", "ts_utc": f"2025-10-10T01:{i:02d}:00Z"})
        checkins.append({"pid": pid, "booth_id": "②", "ts_utc": f"2025-10-10T01:{i + 30:02d}:00Z"})
        bingo_card.append({"pid": pid, "booth_id": "①", "position": 5, "is_recommendation": True})
        bingo_card.append({"pid": pid, "booth_id": "②", "position": 1, "is_recommendation": False})
        users.append(
            {
                "pid": pid,
                "age": "20代" if i % 2 else "30代",
                "gender": "male" if i % 3 else "female",
                "genre": "food",
                "gachapon_coins_spent": 0,
            }
        )
    booths = [
        {"booth_id": "①", "booth_no": 1, "booth_name": "A", "booth_description": "d", "booth_emoji": "x"},
        {"booth_id": "②", "booth_no": 2, "booth_name": "B", "booth_description": "d", "booth_emoji": "x"},
    ]
    return {
        "booths": booths,
        "users": users,
        "checkins": checkins,
        "bingo_card": bingo_card,
        "awards": [],
        "user_status": [],
    }


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

    monkeypatch.chdir(tmp_path)  # dashboard reads the relative path data/tables
    return tables_dir


def test_app_runs_without_exception(tables):
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception


def test_app_reports_missing_tables(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("中間テーブルがありません" in e.value for e in at.error)


def test_day_filter_narrows_population(tables):
    at = AppTest.from_file(APP, default_timeout=60).run()
    both = at.metric[0].value

    at.sidebar.radio[0].set_value("土曜 (10/11)").run()
    assert not at.exception
    # 合成データは金曜のみ。土曜に絞ると該当者が消え、小セル抑制が働く
    assert any("名未満の集計" in w.value for w in at.warning)
    assert both == "25"


def test_small_cell_suppression_blocks_narrow_filters(tables):
    at = AppTest.from_file(APP, default_timeout=60).run()
    # 年代を1つに絞ってもまだ十分な人数がいることを確認してから、
    # さらに存在しない組み合わせへ絞る
    at.sidebar.multiselect[0].set_value(["20代"]).run()
    assert not at.exception
    at.sidebar.multiselect[2].set_value([]).run()
    assert any("名未満の集計" in w.value for w in at.warning)
