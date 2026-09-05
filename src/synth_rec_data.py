"""リハーサル用の合成データ生成（仕様 03 §6「合成データで通しで動かす」）。

当日13時に初めて開くダッシュボードは役に立たない。事前に:
- 合成データで通しで動かす
- **推薦エンジンを止めた状態で、フォールバック率が 🔴 になること**（`--recommender-dead`）
- `/ops/state` が取れないとき、他の指標が動き続けること（`--no-ops-state`）

出力は `rec_db.DumpSource` / `SynthSource` がそのまま読める形（`<table>.csv`）＋
`ops_state.json`（`/ops/state` の疑似応答）＋ `rules_built.jsonl`。

使い方:
    python src/synth_rec_data.py --out data/synth --recommender-dead
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

INTEREST_MATCH = ["MATCH", "PARTIAL", "MISMATCH", "UNKNOWN"]
EVENT_DAY = pd.Timestamp("2026-10-16 09:00:00", tz="Asia/Tokyo").tz_convert("UTC")
EVENT_ID = "evt-2026-protofes4"

# 列は event-support-server の db/create-tables.sql に合わせる。
# とくに card_unlock_events / bingo_cells は **user_id を持たない**（card_id 経由）。
# ここを実物とずらすと、SqlSource へ差し替えた瞬間に全部壊れる。


def generate(n_users: int = 110, seed: int = 20261016, *, recommender_dead: bool = False,
             split_started: bool = True) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)

    users = pd.DataFrame({
        "id": [f"u{i:04d}" for i in range(n_users)],
        "role": ["participant"] * (n_users - 3) + ["staff"] * 3,
    })
    participant_ids = users.loc[users["role"] == "participant", "id"].tolist()

    booths = pd.DataFrame({
        "id": [f"b{i:02d}" for i in range(40)],
        "name": [f"ブース{i:02d}" for i in range(40)],
        "category_id": [f"c{i % 8}" for i in range(40)],
    })
    booth_ids = booths["id"].tolist()

    cards = pd.DataFrame({
        "id": [f"card-{u}" for u in participant_ids],
        "event_id": EVENT_ID,
        "user_id": participant_ids,
    })
    card_of = dict(zip(participant_ids, cards["id"]))

    check_ins, unlock_events, scores, ratings, cells = [], [], [], [], []
    checkin_seq = 0

    for uidx, uid in enumerate(participant_ids):
        card_id = card_of[uid]
        arrival = EVENT_DAY + pd.Timedelta(minutes=int(rng.integers(60, 360)))
        n_visits = int(np.clip(rng.poisson(7), 1, 20))
        # チェックイン
        visit_booths = rng.choice(booth_ids, size=n_visits, replace=False)
        t = arrival
        user_checkin_ids = []
        for order, b in enumerate(visit_booths, start=1):
            t = t + pd.Timedelta(minutes=int(rng.integers(4, 18)))
            checkin_seq += 1
            cid = checkin_seq
            user_checkin_ids.append((cid, b))
            on_card = rng.random() > 0.25
            check_ins.append({
                "id": cid, "user_id": uid, "booth_id": b, "event_id": EVENT_ID,
                "cell_id": (f"cell-{card_id}-{order}" if on_card else None),
                "visit_order": order, "checked_in_at": t.isoformat(),
            })
            if rng.random() < 0.32:  # 評価回収率 ~32%
                ratings.append({
                    "checkin_id": cid, "user_id": uid, "booth_id": b, "event_id": EVENT_ID,
                    "rating": int(rng.integers(1, 5)), "scale": 4,
                    "rated_at": (t + pd.Timedelta(minutes=1)).isoformat(),
                })

        # 解放イベント（1人あたり1〜3回）
        n_unlock = int(rng.integers(1, 4))
        table_size = 0
        for k in range(n_unlock):
            ut = arrival + pd.Timedelta(minutes=20 * (k + 1) + int(rng.integers(0, 10)))
            table_size += int(rng.integers(24, 42))
            phase = "COVERAGE" if table_size < 30 else ("SIMILARITY" if table_size < 60 else "DRSA")
            if recommender_dead:
                strategy = "FALLBACK_COVERAGE"
            else:
                strategy = "FALLBACK_COVERAGE" if rng.random() < 0.05 else "RECOMMEND"
            unlock_id = f"unlock-{card_id}-{k}"
            unlock_events.append({
                "id": unlock_id, "card_id": card_id,  # user_id は持たない（実スキーマ）
                "strategy": strategy, "phase": phase,
                "decision_table_size": table_size,
                "global_checkin_count": checkin_seq, "created_at": ut.isoformat(),
            })

            # この解放で開くマスぶんの推薦スコア（候補全件のうち上位を was_assigned=1）
            n_slots = 2 * (k + 1)
            in_drsa = phase == "DRSA" and split_started
            cand = rng.choice(booth_ids, size=min(12, len(booth_ids)), replace=False)
            cand_scores = np.sort(rng.random(len(cand)))[::-1]
            for ci_, (b, sc_val) in enumerate(zip(cand, cand_scores)):
                assigned = 1 if ci_ < n_slots else 0
                attributes = {"v": 1, "strategy": phase, "enabled": ["preference_match", "rating_affinity"]}
                if in_drsa:
                    attributes["arm"] = "DRSA" if (ci_ % 2 == 0) else "COVERAGE"
                    attributes["split_seed"] = f"{uid}-{k}"
                reason = {"rules": [{"id": f"R{int(rng.integers(1, 15))}"}]} if in_drsa else {}
                scores.append({
                    "id": f"score-{unlock_id}-{ci_}", "unlock_event_id": unlock_id,
                    "user_id": uid, "booth_id": b, "was_assigned": assigned,
                    "score": round(float(sc_val), 3),
                    "rank_in_event": ci_ + 1,
                    "interest_match": INTEREST_MATCH[int(rng.integers(0, 4))],
                    "attributes": json.dumps(attributes),
                    "reason_payload": json.dumps(reason),
                    "created_at": ut.isoformat(),
                })
                revealed = 1 if assigned else 0
                position = len(cells) % 16
                cells.append({
                    "id": f"cell-{card_id}-{len(cells)}", "card_id": card_id,  # user_id は持たない
                    "position": position, "booth_id": (b if revealed else None),
                    "is_revealed": revealed, "is_achieved": 0, "source": "RECOMMEND",
                })

    tables = {
        "users": users,
        "booths": booths,
        "bingo_cards": cards,
        "check_ins": pd.DataFrame(check_ins),
        "card_unlock_events": pd.DataFrame(unlock_events),
        "recommendation_scores": pd.DataFrame(scores),
        "booth_ratings": pd.DataFrame(ratings),
        "bingo_cells": pd.DataFrame(cells),
    }
    return tables


def ops_state(recommender_dead: bool) -> dict:
    dead = recommender_dead
    return {
        "engine_version": "synth",
        "snapshot": {"built_at": (EVENT_DAY + pd.Timedelta(hours=4)).isoformat(), "ok": not dead,
                     "decision_table_size": 0 if dead else 42},
        "rules": {"built_at": (EVENT_DAY + pd.Timedelta(hours=4)).isoformat(),
                  "count_certain_up": 0 if dead else 3, "count_certain_down": 0 if dead else 2,
                  "gamma": 0.0 if dead else 0.62, "candidate_coverage": 0.0 if dead else 0.58,
                  "consistency_level": 0.9},
        "phase": {"current": "COVERAGE" if dead else "DRSA", "judged": "COVERAGE" if dead else "DRSA",
                  "quality_gate_passed": not dead,
                  "gate_detail": {"size": not dead, "rules": not dead, "gamma": not dead, "coverage": not dead}},
        "experiment": {"split_active": not dead, "split_started_at": None},
        "notes": [],
    }


def rules_built_log() -> list[dict]:
    return [{
        "rules_built_at": (EVENT_DAY + pd.Timedelta(hours=h)).isoformat(),
        "rules": [
            {"id": "R12", "antecedent": {"preference_match": 3}, "direction": "up",
             "support": 0.21, "confidence": 0.78},
            {"id": "R7", "antecedent": {"rating_affinity": 2}, "direction": "up",
             "support": 0.15, "confidence": 0.71},
            {"id": "R3", "antecedent": {"visitor_count": 20}, "direction": "down",
             "support": 0.12, "confidence": 0.66},
        ],
    } for h in (4, 5, 6)]


def write(out: Path, *, recommender_dead: bool, with_ops_state: bool, split_started: bool) -> None:
    out.mkdir(parents=True, exist_ok=True)
    tables = generate(recommender_dead=recommender_dead, split_started=split_started)
    for name, df in tables.items():
        df.to_csv(out / f"{name}.csv", index=False)
    if with_ops_state:
        (out / "ops_state.json").write_text(
            json.dumps(ops_state(recommender_dead), ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "rules_built.jsonl").open("w", encoding="utf-8") as f:
        for rec in rules_built_log():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"wrote synthetic dataset to {out}"
          f"{'  [recommender-dead]' if recommender_dead else ''}"
          f"{'  [no ops_state]' if not with_ops_state else ''}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="data/synth", type=Path)
    p.add_argument("--recommender-dead", action="store_true",
                   help="推薦エンジンが死んでいる状態。フォールバック率が 🔴 になるはず")
    p.add_argument("--no-ops-state", action="store_true", help="/ops/state を書き出さない（取得不能の再現）")
    p.add_argument("--no-split", action="store_true", help="参加者内ランダム化を未発動にする")
    return p.parse_args()


def main() -> None:
    a = parse_args()
    write(a.out, recommender_dead=a.recommender_dead, with_ops_state=not a.no_ops_state,
          split_started=not a.no_split)


if __name__ == "__main__":
    main()
