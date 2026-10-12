"""推薦エンジンの中身タブの指標と、JST 保存列の補正（02 §2.1）。"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import live_metrics as lm  # noqa: E402
import rec_db  # noqa: E402

NOW = pd.Timestamp("2026-11-01 05:00", tz="UTC")


def _ratings_checkins(offset_h: float):
    ci = pd.DataFrame({"id": ["c1", "c2"], "user_id": ["u1", "u1"], "booth_id": ["b1", "b2"],
                       "checked_in_at": pd.to_datetime(["2026-11-01 04:00", "2026-11-01 04:10"], utc=True)})
    r = pd.DataFrame({"checkin_id": ["c1", "c2"], "user_id": ["u1", "u1"], "booth_id": ["b1", "b2"],
                      "rating": [4, 1], "scale": [4, 4],
                      "rated_at": ci["checked_in_at"] + pd.Timedelta(hours=offset_h, minutes=1)})
    return ci, r


class AlignTimestamps(unittest.TestCase):
    def test_jst_stored_columns_are_shifted_back(self):
        ci, r = _ratings_checkins(9)
        u = pd.DataFrame({"created_at": pd.to_datetime(["2026-11-01 13:00"], utc=True)})
        out = rec_db.align_db_default_timestamps({"check_ins": ci, "booth_ratings": r, "card_unlock_events": u})
        self.assertEqual(out["booth_ratings"]["rated_at"].iloc[0], pd.Timestamp("2026-11-01 04:01", tz="UTC"))
        self.assertEqual(out["card_unlock_events"]["created_at"].iloc[0], pd.Timestamp("2026-11-01 04:00", tz="UTC"))
        self.assertEqual(out["check_ins"]["checked_in_at"].iloc[0], ci["checked_in_at"].iloc[0])

    def test_utc_data_is_untouched(self):
        ci, r = _ratings_checkins(0)
        out = rec_db.align_db_default_timestamps({"check_ins": ci, "booth_ratings": r})
        self.assertIs(out["booth_ratings"], r)

    def test_future_timestamps_without_ratings_mean_jst(self):
        u = pd.DataFrame({"created_at": [NOW + pd.Timedelta(hours=8)]})
        self.assertEqual(rec_db.jst_stored_offset({"card_unlock_events": u}, NOW), pd.Timedelta(hours=9))


OPS = {
    "snapshot": {"decision_table_size": 30},
    "rules": {"count_certain_up": 5, "count_certain_down": 0, "gamma": 1.0, "candidate_coverage": 1.0,
              "base_rate_high": 0.7},
    "phase": {"current": "COVERAGE", "judged": "COVERAGE", "quality_gate_passed": False,
              "gate_detail": {"size": False, "rules": False, "gamma": True, "coverage": True},
              "gate_stats": {"rules": 0, "min_lift": 0.1}},
    "ser": {"active": False, "mismatch_rows": 12, "min_rows": 20,
            "used": {"by_strategy": {"RANDOM": 35, "DRSA": 0, "SER": 0, "EXPLORE": 5}}},
    "config": {"phase_drsa_min": 60},
}


class EngineOverview(unittest.TestCase):
    def test_remaining_and_eta_from_rating_pace(self):
        r = pd.DataFrame({"rated_at": [NOW - pd.Timedelta(minutes=m) for m in range(0, 30, 2)]})  # 15件/30分
        ov = lm.engine_overview(OPS, r, NOW)
        self.assertEqual(ov["remaining"], 30)
        self.assertAlmostEqual(ov["eta_min"], 60.0)
        self.assertEqual([g["key"] for g in ov["gate_items"] if g["ok"] is False], ["size", "rules"])

    def test_no_pace_means_no_eta(self):
        ov = lm.engine_overview(OPS, pd.DataFrame({"rated_at": [NOW - pd.Timedelta(hours=2)]}), NOW)
        self.assertIsNone(ov["eta_min"])

    def test_unavailable_ops_returns_none(self):
        self.assertIsNone(lm.engine_overview(None, pd.DataFrame(), NOW))


class DecisionTableAndOutcomes(unittest.TestCase):
    def setUp(self):
        self.ci, self.r = _ratings_checkins(0)
        self.scores = pd.DataFrame({
            "user_id": ["u1", "u1", "u1"], "booth_id": ["b1", "b2", "b3"], "was_assigned": [1, 1, 1],
            "interest_match": ["MATCH", "MISMATCH", "MISMATCH"],
            "attributes": ['{"condition": {"preference_match": 2, "rating_affinity": 2}}',
                           '{"condition": {"preference_match": 0, "rating_affinity": 2}}',
                           '{"condition": {"preference_match": 0, "rating_affinity": 2}}'],
            "created_at": pd.to_datetime(["2026-11-01 03:00"] * 3, utc=True)})

    def test_decision_table_rows(self):
        dt = lm.decision_table(self.scores, self.r, self.ci)
        self.assertEqual(dt["decision"].tolist(), ["高評価", "低評価"])
        grid = lm.decision_table_grid(dt)
        self.assertEqual(len(grid), 2)

    def test_outcomes(self):
        oc = lm.recommendation_outcomes(self.scores, self.ci, self.r)
        self.assertAlmostEqual(oc["visit_rate"], 2 / 3)
        self.assertEqual(oc["rec_high_rate"], 0.5)
        self.assertIsNone(oc["off_high_rate"])


if __name__ == "__main__":
    unittest.main()
