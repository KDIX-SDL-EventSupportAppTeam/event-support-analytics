import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import post_eval_metrics as pem  # noqa: E402
import synth_rec_data as synth  # noqa: E402


@pytest.fixture(scope="module")
def tables():
    return synth.generate(n_users=110, recommender_dead=False, split_started=True)


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
