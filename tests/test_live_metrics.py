import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import live_metrics as lm  # noqa: E402
import rec_db  # noqa: E402
import synth_rec_data as synth  # noqa: E402

NOW = pd.Timestamp("2026-10-16T06:00:00Z")


def _unlocks(strategies, base="2026-10-16T05:40:00Z"):
    t0 = pd.Timestamp(base)
    return pd.DataFrame({
        "user_id": [f"u{i}" for i in range(len(strategies))],
        "strategy": strategies,
        "phase": ["DRSA"] * len(strategies),
        "decision_table_size": [80] * len(strategies),
        "global_checkin_count": range(len(strategies)),
        "created_at": [t0 + pd.Timedelta(minutes=i) for i in range(len(strategies))],
    })


def test_fallback_rate_red_when_recommender_dead():
    ue = _unlocks(["FALLBACK_COVERAGE"] * 8 + ["RECOMMEND"] * 2)
    sig = lm.fallback_rate(ue, NOW)
    assert sig.value == pytest.approx(0.8)
    assert sig.level == lm.RED
    assert "再起動" in sig.action


def test_fallback_rate_green_when_healthy():
    ue = _unlocks(["RECOMMEND"] * 19 + ["FALLBACK_COVERAGE"])
    assert lm.fallback_rate(ue, NOW).level == lm.GREEN


def test_fallback_rate_ignores_events_outside_window():
    old = _unlocks(["FALLBACK_COVERAGE"] * 5, base="2026-10-16T04:00:00Z")
    assert lm.fallback_rate(old, NOW, window_min=30).level == lm.UNKNOWN


def test_rating_recovery_rate_red_below_25pct():
    checkins = pd.DataFrame({"id": range(100)})
    ratings = pd.DataFrame({"checkin_id": range(20)})
    sig = lm.rating_recovery_rate(ratings, checkins)
    assert sig.value == pytest.approx(0.2)
    assert sig.level == lm.RED


def test_current_phase_red_when_still_coverage_at_15h():
    ue = _unlocks(["RECOMMEND"], base="2026-10-16T06:30:00Z")  # 15:30 JST
    ue["phase"] = "COVERAGE"
    assert lm.current_phase(ue).level == lm.RED


def test_ops_state_none_reports_unavailable_without_crashing():
    sigs = lm.ops_state_signals(None)
    assert [s.level for s in sigs] == [lm.UNKNOWN] * 3
    assert all(s.value == "取得不能" for s in sigs)


def test_signal_board_survives_missing_ops_state():
    ue = _unlocks(["RECOMMEND"] * 30)
    board = lm.signal_board(ue, pd.DataFrame({"id": [1]}), pd.DataFrame({"checkin_id": [1]}), None, NOW)
    assert lm.worst_level(board) in {lm.GREEN, lm.YELLOW, lm.RED, lm.UNKNOWN}
    assert any(s.key == "fallback_rate" for s in board)


def test_experiment_progress_reports_only_denominators():
    scores = pd.DataFrame({
        "user_id": ["u1", "u1", "u2", "u2"],
        "booth_id": ["b1", "b2", "b3", "b4"],
        "attributes": ['{"arm": "DRSA"}', '{"arm": "COVERAGE"}', '{"arm": "DRSA"}', "{}"],
        "created_at": [NOW] * 4,
    })
    prog = lm.experiment_progress(scores)
    assert prog["split_started"] is True
    assert prog["by_arm"] == {"DRSA": 2, "COVERAGE": 1}
    assert prog["n_participants"] == 2
    # 訪問率・差の類を絶対に返さない（03 §5）
    assert not any("rate" in k or "diff" in k or "visit" in k for k in prog)


def test_live_metrics_module_has_no_per_arm_visit_rate():
    """当日コードに訪問率の群別集計を書かない（03 §5）。関数名レベルで担保する。"""
    banned = [n for n in dir(lm) if ("arm" in n.lower() and ("rate" in n.lower() or "visit" in n.lower()))]
    assert banned == []


def test_assignment_concentration_flags_popularity_collapse():
    # b0 に集中
    scores = pd.DataFrame({
        "booth_id": ["b0"] * 90 + [f"b{i}" for i in range(1, 11)],
        "was_assigned": [1] * 100,
    })
    out = lm.assignment_concentration(scores)
    assert out["top1_share"] == pytest.approx(0.9)
    assert out["gini"] > 0.7


def test_time_series_is_single_frame_with_cumulative_columns():
    tables = synth.generate(n_users=30, recommender_dead=False)
    ts = lm.time_series(tables["check_ins"], tables["booth_ratings"], tables["card_unlock_events"])
    assert {"bin", "cum_checkins", "cum_ratings", "decision_table_size"} <= set(ts.columns)
    assert ts["cum_checkins"].is_monotonic_increasing


def test_synth_recommender_dead_drives_fallback_red():
    tables = synth.generate(n_users=60, recommender_dead=True)
    ue = tables["card_unlock_events"]
    now = pd.to_datetime(ue["created_at"], utc=True).max()
    assert lm.fallback_rate(ue, now, window_min=600).level == lm.RED


def test_ops_state_signals_distinguish_auth_error():
    """**設定漏れとエンジン停止を画面上で区別する**（推薦側 ADR 0008 Q-1）。"""
    unavailable = lm.ops_state_signals(None)
    auth = lm.ops_state_signals(None, rec_db.OPS_AUTH_ERROR)

    assert [s.level for s in auth] == [lm.UNKNOWN] * 3  # 品質ゲートの扱いは変えない
    assert all(s.value == "取得不能" for s in unavailable)
    assert all(s.value == "認証エラー" for s in auth)
    assert all("RECOMMEND_OPS_TOKEN" in s.detail for s in auth)


def test_ops_status_vocabulary_matches_rec_db():
    """`live_metrics` は取得層に依存しないよう定数を再定義している。**ずれたら無言で壊れる。**

    `rec_db` が返す文字列と `live_metrics` が比較する文字列が一致しなくなると、
    認証エラーが「取得不能」に戻り、当日の切り分けができなくなる。
    """
    assert lm.OPS_AUTH_ERROR == rec_db.OPS_AUTH_ERROR
    assert lm.TOKEN_ENV == rec_db.OpsStateClient.TOKEN_ENV
    assert lm.OPS_TOKEN_MISSING == rec_db.OPS_TOKEN_MISSING


def test_normalize_ops_state_reads_nested_production_shape():
    payload = {"snapshot": {"built_at": "2026-10-16T04:00:00+00:00", "ok": True, "decision_table_size": 37},
               "rules": {"built_at": "2026-10-16T04:00:00+00:00", "count_certain_up": 3, "count_certain_down": 1,
                         "gamma": 0.7, "candidate_coverage": 0.4, "consistency_level": 0.9},
               "phase": {"current": "SIMILARITY", "judged": "DRSA", "quality_gate_passed": False,
                         "gate_detail": {"size": True, "rules": True, "gamma": True, "coverage": False}}}
    s = lm.normalize_ops_state(payload)
    assert (s["gamma"], s["n_certain_rules"], s["rule_coverage"]) == (0.7, 4, 0.4)
    assert s["latency_p95_ms"] is None                      # 無いものは None。0 にしない
    assert (s["phase_current"], s["decision_table_size"]) == ("SIMILARITY", 37)
    assert s["gate_detail"] == {"size": True, "rules": True, "gamma": True, "coverage": False}  # T-9: 個別に読める


def test_normalize_ops_state_keeps_flat_synth_shape():
    s = lm.normalize_ops_state({"gamma": 0.62, "n_certain_rules": 5, "rule_coverage": 0.58, "latency_p95_ms": 320})
    assert (s["gamma"], s["n_certain_rules"], s["rule_coverage"], s["latency_p95_ms"]) == (0.62, 5, 0.58, 320)
    assert s["phase_current"] is None and s["gate_detail"] is None


def test_gate_detail_signal_lists_failed_items_individually():
    sig = lm.gate_detail_signal(lm.normalize_ops_state(
        {"phase": {"quality_gate_passed": False,
                   "gate_detail": {"size": True, "rules": False, "gamma": True, "coverage": False}}}))
    assert "rules" in sig.value and "coverage" in sig.value and "size" not in sig.value
    assert sig.level == lm.YELLOW


def test_ops_state_signals_distinguish_token_missing():
    sigs = lm.ops_state_signals(None, rec_db.OPS_TOKEN_MISSING)
    assert all(s.value == "未設定" for s in sigs)
    assert all("RECOMMEND_OPS_TOKEN" in s.detail for s in sigs)
    assert lm.OPS_TOKEN_MISSING == rec_db.OPS_TOKEN_MISSING


def test_synth_ops_state_is_production_shaped():
    s = lm.normalize_ops_state(synth.ops_state(recommender_dead=False))
    assert s["phase_current"] == "DRSA" and s["decision_table_size"] == 42 and s["n_certain_rules"] == 5
