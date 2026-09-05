import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from build_tables import (  # noqa: E402
    apply_staff_exclusion,
    build_booths,
    build_participants,
    build_visits,
    detect_staff_candidates,
)


def make_raw():
    return {
        "booths": [
            {"booth_id": "①", "booth_no": 1, "booth_name": "A" * 30, "booth_description": "d", "booth_emoji": "🍙"},
            {"booth_id": "②", "booth_no": 2, "booth_name": "B", "booth_description": "d", "booth_emoji": "🍙"},
        ],
        "users": [
            {"pid": "u0001", "age": "20代", "gender": "male", "genre": "food", "gachapon_coins_spent": 0},
            {"pid": "u0002", "age": "30代", "gender": "female", "genre": "tech", "gachapon_coins_spent": 0},
        ],
        "checkins": [
            # u0001: day1, two booths, 40 min apart
            {"pid": "u0001", "booth_id": "①", "ts_utc": "2025-10-10T01:00:00Z"},  # 10:00 JST
            {"pid": "u0001", "booth_id": "②", "ts_utc": "2025-10-10T01:40:00Z"},  # 10:40 JST
            # u0002: single visit
            {"pid": "u0002", "booth_id": "①", "ts_utc": "2025-10-10T02:00:00Z"},
            # out-of-period noise (test account)
            {"pid": "u0002", "booth_id": "②", "ts_utc": "2025-10-27T02:00:00Z"},
        ],
        "bingo_card": [
            {"pid": "u0001", "booth_id": "①", "position": 5, "is_recommendation": True},
            {"pid": "u0001", "booth_id": "②", "position": 6, "is_recommendation": True},
        ],
        "awards": [{"pid": "u0001", "award_name": "best", "booth_id": "①", "ts_utc": "2025-10-10T03:00:00Z"}],
        "user_status": [{"pid": "u0001", "vote_finalized": 1}],
    }


def test_visits_excludes_out_of_period_and_computes_gap():
    raw = make_raw()
    booths = build_booths(raw)
    visits, stats = build_visits(raw, booths)

    assert stats["excluded_out_of_period"] == 1
    assert len(visits) == 3

    u1 = visits[visits["pid"] == "u0001"].sort_values("visit_seq")
    assert list(u1["visit_seq"]) == [1, 2]
    assert u1["gap_min"].isna().iloc[0]
    assert abs(u1["gap_min"].iloc[1] - 40) < 1e-6


def test_participants_includes_zero_checkin_users_and_bingo_hits():
    raw = make_raw()
    raw["users"].append({"pid": "u0003", "age": "40代", "gender": "male", "genre": "art", "gachapon_coins_spent": 0})
    booths = build_booths(raw)
    visits, _ = build_visits(raw, booths)
    participants = build_participants(raw, visits)

    zero_row = participants[participants["pid"] == "u0003"]
    assert len(zero_row) == 1
    assert zero_row["n_booths"].iloc[0] == 0
    assert zero_row["day"].iloc[0] is None

    u1_row = participants[participants["pid"] == "u0001"].iloc[0]
    assert u1_row["is_single"] == False  # noqa: E712
    assert u1_row["n_card_hit"] == 2
    assert u1_row["n_rec_hit"] == 2
    assert u1_row["dwell_min"] == 40

    u2_row = participants[participants["pid"] == "u0002"].iloc[0]
    assert u2_row["is_single"] == True  # noqa: E712
    assert u2_row["dwell_min"] == 0
    assert u2_row["voted"] == False  # noqa: E712
    assert u1_row["voted"] == True  # noqa: E712
    assert u1_row["vote_finalized"] == True  # noqa: E712


def test_staff_candidate_detection_flags_pre_open_checkin():
    raw = make_raw()
    raw["checkins"].append({"pid": "u0002", "booth_id": "①", "ts_utc": "2025-10-09T22:00:00Z"})  # 07:00 JST
    booths = build_booths(raw)
    visits, _ = build_visits(raw, booths)
    # the pre-open checkin is itself out of the event window and dropped by build_visits;
    # use a within-window early hour instead to exercise the heuristic directly
    visits.loc[visits["pid"] == "u0002", "ts_jst"] = visits.loc[
        visits["pid"] == "u0002", "ts_jst"
    ].apply(lambda ts: ts.replace(hour=7))

    candidates = detect_staff_candidates(visits, booths, open_hour_jst=9)
    assert "u0002" in set(candidates["pid"])


def test_apply_staff_exclusion_records_pids_and_counts():
    visits = pd.DataFrame({"pid": ["u0001", "u0001", "u0002", "u0003"], "booth_id": ["b1", "b2", "b1", "b1"]})
    stats: dict = {}
    kept = apply_staff_exclusion(visits, ["u0002", " u0002 "], stats)
    assert set(kept["pid"]) == {"u0001", "u0003"}
    assert stats == {"excluded_pids": ["u0002"], "excluded_staff_users": 1,
                     "visits_after_staff_exclusion_users": 2}


def test_apply_staff_exclusion_without_pids_changes_nothing_but_still_records():
    visits = pd.DataFrame({"pid": ["u0001", "u0002"], "booth_id": ["b1", "b1"]})
    stats: dict = {}
    assert apply_staff_exclusion(visits, [], stats).equals(visits)
    assert stats["excluded_pids"] == [] and stats["excluded_staff_users"] == 0
