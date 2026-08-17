import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from build_tables import build_booths, build_participants, build_visits  # noqa: E402
import metrics  # noqa: E402


def make_raw():
    checkins = []
    bingo_card = []
    # 25 participants on Friday, each visiting booth 1 then booth 2, 10 min apart
    for i in range(1, 26):
        pid = f"u{i:04d}"
        base_minute = i  # stagger first-checkin times
        checkins.append({"pid": pid, "booth_id": "①", "ts_utc": f"2025-10-10T01:{base_minute:02d}:00Z"})
        checkins.append({"pid": pid, "booth_id": "②", "ts_utc": f"2025-10-10T01:{base_minute + 10:02d}:00Z"})
        bingo_card.append({"pid": pid, "booth_id": "①", "position": 5, "is_recommendation": True})
        bingo_card.append({"pid": pid, "booth_id": "②", "position": 1, "is_recommendation": False})

    users = [
        {"pid": f"u{i:04d}", "age": "20代", "gender": "male", "genre": "food", "gachapon_coins_spent": 0}
        for i in range(1, 26)
    ]
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


def build_all():
    raw = make_raw()
    booths = build_booths(raw)
    visits, _ = build_visits(raw, booths)
    participants = build_participants(raw, visits)
    return visits, participants, booths


def test_recommendation_fallback_release_time_after_20th_participant():
    visits, participants, booths = build_all()
    day = metrics.FRIDAY
    t = metrics.recommendation_fallback_release_time(visits, day)
    assert t is not None
    # the 21st distinct participant (index 20, i=21) first checks in at minute :21
    assert ":21:" in t


def test_recommendation_effect_perfect_for_recommended_slots():
    visits, participants, booths = build_all()
    effect = metrics.recommendation_effect(participants)
    assert effect["recommended_hit_rate"] == 1.0
    assert effect["random_hit_rate"] == 1.0  # both booths checked in this fixture


def test_booth_skew_stats_ratio():
    visits, participants, booths = build_all()
    ranking = metrics.booth_visit_ranking(visits, booths)
    stats = metrics.booth_skew_stats(ranking)
    assert stats["max_visits"] == 25
    assert stats["min_visits"] == 25
    assert stats["ratio_max_to_min"] == 1.0


def test_dwell_time_stats_excludes_single_from_central_stats():
    visits, participants, booths = build_all()
    stats = metrics.dwell_time_stats(participants, metrics.FRIDAY)
    assert stats["n_single"] == 0
    assert stats["median"] == 10.0
