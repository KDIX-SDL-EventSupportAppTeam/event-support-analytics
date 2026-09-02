"""当日・事後の Streamlit 画面が合成データで例外なく起動することの確認（03 §6 リハーサル）。"""

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import live_dashboard  # noqa: E402
import rec_db  # noqa: E402
import live_dashboard  # noqa: E402
import rec_db  # noqa: E402
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


# --- 取得口の差し替え（02 §4）------------------------------------------------
#
# **実 DB には接続しない。** 「どちらを組み立てるか」と「画面が落ちないか」だけを見る。


def test_default_source_is_synth():
    """**既定を本番にしない。** 口が無い状態で既定を変えると全画面が落ちる。"""
    assert live_dashboard.SOURCE_KIND == "synth"


def test_make_source_builds_synth_by_default(synth_dir):
    src = live_dashboard.make_source("synth", str(synth_dir))
    assert isinstance(src, rec_db.SynthSource)


def test_make_source_builds_sql_when_asked(monkeypatch, synth_dir):
    """REC_SOURCE=sql のとき、ディレクトリではなく読み取り専用プロキシを見る。"""
    monkeypatch.setenv(rec_db.SqlSource.URL_ENV, "https://proxy.invalid/readonly")
    monkeypatch.setenv(rec_db.SqlSource.KEY_ENV, "dummy")
    src = live_dashboard.make_source("sql", str(synth_dir))
    assert isinstance(src, rec_db.SqlSource)


def test_source_label_names_which_one_is_shown(synth_dir):
    """**今どちらを見ているかを画面に必ず出す。** 取り違えると監視が意味を失う。"""
    assert "合成" in live_dashboard.source_label("synth", str(synth_dir))
    assert "本番" in live_dashboard.source_label("sql", str(synth_dir))


def test_live_dashboard_survives_missing_proxy_credentials(monkeypatch, synth_dir):
    """REC_SOURCE=sql なのに口が未設定でも、例外で画面を殺さずエラー表示で止める。"""
    monkeypatch.setenv("REC_SOURCE", "sql")
    monkeypatch.delenv(rec_db.SqlSource.URL_ENV, raising=False)
    monkeypatch.delenv(rec_db.SqlSource.KEY_ENV, raising=False)
    at = AppTest.from_file(LIVE_APP, default_timeout=60)
    at.run()
    assert not at.exception
    assert any(rec_db.SqlSource.URL_ENV in e.value for e in at.error)


# --- 取得口の差し替え（02 §4）------------------------------------------------
#
# **実 DB には接続しない。** 「どちらを組み立てるか」と「画面が落ちないか」だけを見る。


def test_default_source_is_synth():
    """**既定を本番にしない。** 口が無い状態で既定を変えると全画面が落ちる。"""
    assert live_dashboard.SOURCE_KIND == "synth"


def test_make_source_builds_synth_by_default(synth_dir):
    src = live_dashboard.make_source("synth", str(synth_dir))
    assert isinstance(src, rec_db.SynthSource)


def test_make_source_builds_sql_when_asked(monkeypatch, synth_dir):
    """REC_SOURCE=sql のとき、ディレクトリではなく読み取り専用プロキシを見る。"""
    monkeypatch.setenv(rec_db.SqlSource.URL_ENV, "https://proxy.invalid/readonly")
    monkeypatch.setenv(rec_db.SqlSource.KEY_ENV, "dummy")
    src = live_dashboard.make_source("sql", str(synth_dir))
    assert isinstance(src, rec_db.SqlSource)


def test_source_label_names_which_one_is_shown(synth_dir):
    """**今どちらを見ているかを画面に必ず出す。** 取り違えると監視が意味を失う。"""
    assert "合成" in live_dashboard.source_label("synth", str(synth_dir))
    assert "本番" in live_dashboard.source_label("sql", str(synth_dir))


def test_live_dashboard_survives_missing_proxy_credentials(monkeypatch, synth_dir):
    """REC_SOURCE=sql なのに口が未設定でも、例外で画面を殺さずエラー表示で止める。"""
    monkeypatch.setenv("REC_SOURCE", "sql")
    monkeypatch.delenv(rec_db.SqlSource.URL_ENV, raising=False)
    monkeypatch.delenv(rec_db.SqlSource.KEY_ENV, raising=False)
    at = AppTest.from_file(LIVE_APP, default_timeout=60)
    at.run()
    assert not at.exception
    assert any(rec_db.SqlSource.URL_ENV in e.value for e in at.error)
