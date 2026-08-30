"""統合アプリ（src/app.py）の確認。

行き来できること・**どの画面も合言葉の外に出ていないこと**を担保する。
"""

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import synth_rec_data as synth  # noqa: E402

APP = str(SRC / "app.py")
PASSWORD = "ひみつの合言葉"


@pytest.fixture()
def synth_dir(tmp_path, monkeypatch):
    synth.write(tmp_path, recommender_dead=False, with_ops_state=True, split_started=True)
    monkeypatch.setenv("REC_DATA_DIR", str(tmp_path))
    return tmp_path


def test_app_offers_all_three_screens(synth_dir):
    at = AppTest.from_file(APP, default_timeout=90)
    at.run()
    assert not at.exception
    # 既定は去年の行動データ
    assert any("来場者行動データ" in m.value for m in at.title)


def test_app_gates_every_screen_behind_the_password(synth_dir, monkeypatch):
    """合言葉が通るまで、どの画面の中身も描画しない。"""
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    at = AppTest.from_file(APP, default_timeout=90)
    at.run()
    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "関係者用ページ" in body
    # どの画面のタイトルも出ていない = メニューごと止まっている
    assert at.title.len == 0


def test_app_shows_screens_after_correct_password(synth_dir, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    at = AppTest.from_file(APP, default_timeout=90)
    at.run()
    at.text_input[0].set_value(PASSWORD)
    at.button[0].click().run()
    assert not at.exception
    assert at.title.len > 0


def test_missing_password_in_container_stops_every_screen(synth_dir, monkeypatch):
    """DASHBOARD_AUTH_REQUIRED=1 かつ合言葉未設定なら、統合アプリごと止まる。"""
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.setenv("DASHBOARD_AUTH_REQUIRED", "1")
    at = AppTest.from_file(APP, default_timeout=90)
    at.run()
    assert not at.exception
    assert any("合言葉が設定されていません" in e.value for e in at.error)
    assert at.title.len == 0


@pytest.mark.parametrize("page", ["live_dashboard", "post_analysis"])
def test_rec_screens_require_password_standalone(synth_dir, monkeypatch, page):
    """単体起動でも合言葉の外に出ていないこと（統合前は無防備だった）。"""
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    at = AppTest.from_file(str(SRC / f"{page}.py"), default_timeout=90)
    at.run()
    assert not at.exception
    assert any("関係者用ページ" in m.value for m in at.markdown)
    assert at.title.len == 0
