"""当日・事後の Streamlit 画面が合成データで例外なく起動することの確認（03 §6 リハーサル）。"""

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import synth_rec_data as synth  # noqa: E402

LIVE_APP = str(SRC / "live_dashboard.py")
POST_APP = str(SRC / "post_analysis.py")


@pytest.fixture()
def synth_dir(tmp_path):
    synth.write(tmp_path, recommender_dead=False, with_ops_state=True, split_started=True)
    return tmp_path


@pytest.fixture()
def synth_dir_dead(tmp_path):
    synth.write(tmp_path, recommender_dead=True, with_ops_state=False, split_started=True)
    return tmp_path


def _run(app_path, source_dir):
    at = AppTest.from_file(app_path, default_timeout=60)
    at.run()
    assert not at.exception
    at.sidebar.text_input[0].set_value(str(source_dir)).run()
    assert not at.exception
    return at


def test_live_dashboard_runs_on_synth(synth_dir):
    at = _run(LIVE_APP, synth_dir)
    assert any("当日監視" in m.value for m in at.title)


def test_live_dashboard_runs_without_ops_state(synth_dir_dead):
    """/ops/state が無くても他の指標が動き続ける（03「/ops/state が取れないとき」）。"""
    at = _run(LIVE_APP, synth_dir_dead)
    assert not at.exception
    assert any("取得不能" in m.value for m in at.info)


def test_live_dashboard_survives_corrupt_ops_state(synth_dir):
    """壊れた ops_state.json で画面全体を落とさない（02 §1）。"""
    (synth_dir / "ops_state.json").write_text("{ this is not json", encoding="utf-8")
    at = _run(LIVE_APP, synth_dir)
    assert not at.exception
    assert any("取得不能" in m.value for m in at.info)


def test_live_dashboard_shows_times_in_jst(synth_dir):
    """当日画面に UTC のまま時刻を出さない（AGENTS.md「必ず JST(+9) へ変換」）。"""
    at = _run(LIVE_APP, synth_dir)
    captions = " ".join(c.value for c in at.caption)
    assert "JST" in captions
    assert "+9h" not in captions  # 暗算を運営に押し付けない


def test_post_analysis_runs_on_synth(synth_dir):
    at = _run(POST_APP, synth_dir)
    assert not at.exception
