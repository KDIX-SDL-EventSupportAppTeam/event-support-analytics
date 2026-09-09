import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import post_eval_metrics as pem  # noqa: E402
import rec_db  # noqa: E402
import synth_rec_data as synth  # noqa: E402


@pytest.fixture(scope="module")
def tables():
    """合成データを、画面と同じ経路（rec_db.load_tables）で整えてから渡す。

    合成データは実スキーマどおり card_unlock_events に user_id を持たないので、
    rec_db を通さずに使うと本番と違う形をテストしてしまう。
    """
    raw = synth.generate(n_users=110, recommender_dead=False, split_started=True)

    class _InMemory:
        def table(self, name):
            return raw[name]

    return rec_db.load_tables(_InMemory())


def test_ecdf_is_monotonic_and_ends_at_one():
    x, y = pem.ecdf([3, 1, 2, 5, 4])
    assert list(x) == [1, 2, 3, 4, 5]
    assert y[-1] == pytest.approx(1.0)
    assert np.all(np.diff(y) > 0)


def test_booth_count_ecdf_includes_last_year_when_participants_given(tables):
    participants = pd.DataFrame({
        "pid": [f"p{i}" for i in range(10)],
        "day": ["2025-10-10"] * 7 + ["2025-10-11"] * 3,
        "n_booths": [5, 6, 7, 4, 8, 6, 6, 99, 99, 99],
    })
    out = pem.booth_count_ecdf(tables["check_ins"], participants)
    assert out["last_year_friday"]["n"] == 7  # 土曜ぶんは除外
    assert out["last_year_friday"]["median"] == 6.0
    assert "this_year" in out


def test_within_participant_diff_is_paired_and_only_arm_rows(tables):
    out = pem.within_participant_diff(tables["recommendation_scores"], tables["check_ins"])
    assert out["n_participants"] == len(out["diffs"])
    assert out["n_participants"] > 0
    assert "対応のある比較" in out["comparison"]
    assert "8ポイント" in out["caveat"]


def test_within_participant_diff_empty_without_arm():
    scores = pd.DataFrame({
        "user_id": ["u1", "u1"], "booth_id": ["b1", "b2"],
        "attributes": ["{}", "{}"], "created_at": [pd.Timestamp("2026-10-16T05:00:00Z")] * 2,
    })
    out = pem.within_participant_diff(scores, pd.DataFrame({"user_id": [], "booth_id": []}))
    assert out["diffs"] == []
    assert out["n_participants"] == 0


def test_interest_match_funnel_monotonic_non_increasing(tables):
    f = pem.interest_match_funnel(
        tables["recommendation_scores"], tables["check_ins"], tables["booth_ratings"])
    assert list(f["interest_match"]) == pem.FUNNEL_MATCH_ORDER
    for _, row in f.iterrows():
        assert row["presented"] >= row["visited"] >= row["rated"] >= row["high"]


def test_funnel_uses_frozen_interest_match_not_recomputed():
    scores = pd.DataFrame({
        "user_id": ["u1"], "booth_id": ["b1"], "was_assigned": [1], "score": [0.9],
        "interest_match": ["MISMATCH"], "attributes": ["{}"], "reason_payload": ["{}"],
        "created_at": [pd.Timestamp("2026-10-16T05:00:00Z")],
    })
    check_ins = pd.DataFrame({"id": [1], "user_id": ["u1"], "booth_id": ["b1"], "cell_id": [1]})
    ratings = pd.DataFrame({"checkin_id": [1], "rating": [4], "scale": [4]})
    f = pem.interest_match_funnel(scores, check_ins, ratings)
    mismatch = f[f["interest_match"] == "MISMATCH"].iloc[0]
    assert mismatch["presented"] == 1 and mismatch["visited"] == 1 and mismatch["high"] == 1


def test_funnel_yields_are_none_not_nan_when_denominator_zero():
    """提示0のとき歩留まりは None。画面側はこれを数値化して整形する（object dtype 対策）。"""
    empty = pd.DataFrame(columns=[
        "user_id", "booth_id", "was_assigned", "score", "interest_match",
        "attributes", "reason_payload", "created_at"])
    f = pem.interest_match_funnel(
        empty, pd.DataFrame(columns=["id", "user_id", "booth_id", "cell_id"]),
        pd.DataFrame(columns=["checkin_id", "rating", "scale"]))
    assert (f["presented"] == 0).all()
    assert f["visit_yield"].isna().all()
    # 画面側の整形（post_analysis.fig3_funnel と同じ式）が例外にならないこと
    formatted = pd.to_numeric(f["visit_yield"], errors="coerce").map(
        lambda v: "—" if pd.isna(v) else f"{v * 100:.1f}%")
    assert list(formatted) == ["—"] * 4


def test_assigned_scores_rank_above_unassigned(tables):
    out = pem.assigned_vs_unassigned_scores(tables["recommendation_scores"])
    assert out["sanity_ok"] is True


def test_rules_table_counts_fires_from_reason_payload():
    records = synth.rules_built_log()
    scores = pd.DataFrame({"reason_payload": [
        '{"rules": [{"id": "R12"}]}', '{"rules": [{"id": "R12"}, {"id": "R7"}]}', "{}",
    ]})
    table = pem.rules_table(records, scores)
    r12 = table[table["rule_id"] == "R12"].iloc[0]
    assert r12["fired"] == 2
    assert "if" in r12["rule"] and r12["direction"] == "上方"


def test_participant_timeline_is_time_sorted(tables):
    uid = tables["check_ins"]["user_id"].iloc[0]
    tl = pem.participant_timeline(
        uid, tables["check_ins"], tables["recommendation_scores"],
        tables["card_unlock_events"], tables["booth_ratings"])
    ats = [e["at"] for e in tl]
    assert ats == sorted(ats)
    assert len(tl) > 0


def test_visit_rate_by_decision_table_band(tables):
    out = pem.visit_rate_by_decision_table_band(
        tables["recommendation_scores"], tables["check_ins"], tables["card_unlock_events"])
    assert {"band", "visit_rate", "n"} <= set(out.columns)
    assert (out["visit_rate"].dropna().between(0, 1)).all()


# --- 図⑧ エンジン状態（/ops/state の凍結値）（issue #18）--------------------


def test_ops_state_summary_none_is_not_zero_filled():
    """取れていないときは available=False。0/空で埋めない（issue #18「起きてはいけないこと」）。"""
    s = pem.ops_state_summary(None)
    assert s["available"] is False
    assert "decision_table_size" not in s and "gate_detail" not in s


def test_ops_state_summary_exposes_gate_detail_individually():
    """T-9: gate_detail の4項目を個別に読める。まとめて1つの真偽値にしない。"""
    payload = {
        "snapshot": {"decision_table_size": 214, "built_at": "2026-10-16T04:35:00Z"},
        "rules": {"built_at": "2026-10-16T04:35:00Z", "gamma": 0.4,
                  "count_certain_up": 1, "count_certain_down": 0, "candidate_coverage": 0.3},
        "phase": {"current": "SIMILARITY", "judged": "SIMILARITY", "quality_gate_passed": False,
                  "gate_detail": {"size": True, "rules": False, "gamma": False, "coverage": True}},
    }
    s = pem.ops_state_summary(payload)
    assert s["available"] is True
    assert s["gate_detail"] == {"size": True, "rules": False, "gamma": False, "coverage": True}
    assert set(s["gate_failed_items"]) == {"rules", "gamma"}
    assert s["decision_table_size"] == 214
    assert s["snapshot_built_at"] == "2026-10-16T04:35:00Z"
    assert "確実規則" in s["gate_reason"] and "γ" in s["gate_reason"]


def test_ops_state_summary_distinguishes_null_gate_from_failure():
    """推薦を1件も処理していない（gate_detail が全 null）を『落ちた』と読まない（02 §2）。"""
    payload = {"snapshot": {"decision_table_size": 12},
               "phase": {"current": "COVERAGE", "quality_gate_passed": None, "gate_detail": None}}
    s = pem.ops_state_summary(payload)
    assert s["gate_failed_items"] == []
    assert "1件も" in s["gate_reason"]


def test_ops_state_summary_reads_synth_shape():
    s = pem.ops_state_summary(synth.ops_state(recommender_dead=False))
    assert s["available"] and s["gate_detail"]["size"] is True
    assert s["latency_p95_ms"] == 112  # 入れ子 latency_ms.p95 を読めている


# --- 図⑨ 推薦パラメータの妥当性検証（issue #11）--------------------------


def test_phase_from_size_uses_count_only_and_distinguishes_null():
    assert pem.phase_from_size(10, 30, 60) == "COVERAGE"
    assert pem.phase_from_size(45, 30, 60) == "SIMILARITY"
    assert pem.phase_from_size(80, 30, 60) == "DRSA"
    assert pem.phase_from_size(None) is None          # 測れなかった。0 と区別する
    assert pem.phase_from_size(float("nan")) is None


def test_phase_comparison_is_descriptive_and_ordered(tables):
    out = pem.phase_comparison(tables["card_unlock_events"], tables["recommendation_scores"],
                               tables["check_ins"])
    assert list(out["phase"]) == ["COVERAGE", "SIMILARITY", "DRSA"]
    assert "交絡" in out.attrs["caveat"]
    assert (out["fallback_rate"].dropna().between(0, 1)).all()


def test_phase_change_times_prefers_log_and_excludes_demo():
    recs = synth.phase_changed_log(recommender_dead=False)
    out = pem.phase_change_times(pd.DataFrame(), recs)
    assert list(out["to"]) == ["SIMILARITY", "DRSA"]          # demo(log_kind=recommend_demo) は除外
    assert (out["source"] == "log(phase_changed)").all()


def test_phase_change_times_falls_back_to_db(tables):
    out = pem.phase_change_times(tables["card_unlock_events"], None)
    assert set(out.columns) >= {"at", "from", "to", "source"}
    if not out.empty:
        assert (out["source"] == "db(card_unlock_events)").all()
        assert (out["from"] != out["to"]).all()


def test_counterfactual_phase_distribution_recomputes_from_size(tables):
    out = pem.counterfactual_phase_distribution(tables["card_unlock_events"])
    assert out.iloc[0]["scenario"].startswith("実測")
    strict = out[out["PHASE_DRSA_MIN"] == 180].iloc[0]
    lenient = out[out["PHASE_DRSA_MIN"] == 45].iloc[0]
    # DRSA_MIN を上げれば DRSA 到達解放数は減る（単調）
    assert strict["DRSA到達 解放数"] <= lenient["DRSA到達 解放数"]


def test_threshold_report_records_unreached_as_result_not_failure(tables):
    ue = tables["card_unlock_events"].copy()
    ue["decision_table_size"] = 12          # どのしきい値にも届かない
    ue["phase"] = "COVERAGE"
    rep = pem.threshold_report(ue, ops_state=None)
    assert rep["max_decision_table_size"] == 12
    assert rep["drsa_phase_ever_used"] is False
    assert "PHASE_SIMILARITY_MIN" in rep["not_reached"]
    assert "失敗ではなく" in rep["summary"]
    # /ops/state 未取得のゲート項目は「判定不能」に入り、0 埋めされない
    assert any("品質ゲート" in p for p in rep["undetermined"])


def test_threshold_report_uses_ops_state_gate_values(tables):
    payload = {"snapshot": {"decision_table_size": 90},
               "rules": {"gamma": 0.3, "count_certain_up": 1, "count_certain_down": 0,
                         "candidate_coverage": 0.2},
               "phase": {"quality_gate_passed": False,
                         "gate_detail": {"size": True, "rules": False, "gamma": False, "coverage": False}}}
    ue = tables["card_unlock_events"].copy()
    ue["decision_table_size"] = 90
    rep = pem.threshold_report(ue, payload)
    assert "DRSA_MIN_GAMMA" in rep["not_reached"]
    assert "DRSA_MIN_RULES" in rep["not_reached"]
