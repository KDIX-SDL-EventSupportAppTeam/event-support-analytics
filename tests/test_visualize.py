import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from build_tables import build_booths, build_participants, build_visits, write_csv  # noqa: E402
import visualize  # noqa: E402


def make_raw():
    checkins, bingo_card, users = [], [], []
    for i in range(1, 26):
        pid = f"u{i:04d}"
        m = i
        checkins.append({"pid": pid, "booth_id": "①", "ts_utc": f"2025-10-10T01:{m:02d}:00Z"})
        checkins.append({"pid": pid, "booth_id": "②", "ts_utc": f"2025-10-10T01:{m + 10:02d}:00Z"})
        bingo_card.append({"pid": pid, "booth_id": "①", "position": 5, "is_recommendation": True})
        users.append({"pid": pid, "age": "20代", "gender": "male", "genre": "food", "gachapon_coins_spent": 0})
    booths = [
        {"booth_id": "①", "booth_no": 1, "booth_name": "A", "booth_description": "d", "booth_emoji": "x"},
        {"booth_id": "②", "booth_no": 2, "booth_name": "B", "booth_description": "d", "booth_emoji": "x"},
    ]
    return {"booths": booths, "users": users, "checkins": checkins, "bingo_card": bingo_card, "awards": [], "user_status": []}


def test_run_all_produces_expected_figures(tmp_path):
    raw = make_raw()
    booths = build_booths(raw)
    visits, _ = build_visits(raw, booths)
    participants = build_participants(raw, visits)

    tables_dir = tmp_path / "tables"
    write_csv(visits, tables_dir / "visits.csv")
    write_csv(participants, tables_dir / "participants.csv")
    write_csv(booths, tables_dir / "booths.csv")

    out_dir = tmp_path / "figures"
    visualize.run_all(tables_dir, out_dir)

    produced = {p.stem for p in out_dir.glob("*.png")}
    assert "D1_booth_ranking" in produced
    assert any(p.startswith("A1_dwell_histogram") for p in produced)
    assert any(p.startswith("C1_booth_count_histogram") for p in produced)
    assert any(p.startswith("B2_B3_flow") for p in produced)
