"""合成データと取得口が、本番スキーマと同じ形であることを固定する。

正本は `event-support-server/db/create-tables.sql`。ここでずれると、
`SqlSource` へ差し替えた瞬間に当日画面が壊れる（ADR 0001「影響」）。

**とくに `card_unlock_events` と `bingo_cells` は `user_id` を持たない。**
指標が使う `user_id` は `card_id` → `bingo_cards` の JOIN で得る。
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import rec_db  # noqa: E402
import synth_rec_data as synth  # noqa: E402

# create-tables.sql から写した「必ず在る列」。網羅ではなく、指標が触る列に絞る。
REAL_COLUMNS = {
    "bingo_cards": {"id", "event_id", "user_id"},
    "card_unlock_events": {"id", "card_id", "phase", "strategy",
                           "decision_table_size", "global_checkin_count", "created_at"},
    "bingo_cells": {"id", "card_id", "position", "booth_id", "is_revealed", "is_achieved", "source"},
    "check_ins": {"id", "user_id", "booth_id", "event_id", "cell_id", "visit_order", "checked_in_at"},
    "booth_ratings": {"checkin_id", "user_id", "booth_id", "event_id", "rating", "scale", "rated_at"},
    "recommendation_scores": {"id", "unlock_event_id", "user_id", "booth_id", "was_assigned",
                              "score", "rank_in_event", "interest_match", "attributes",
                              "reason_payload", "created_at"},
}

# 本番に存在しない列。合成データが持っていたら、実装がその列に依存してしまう。
NOT_IN_REAL_SCHEMA = {
    "card_unlock_events": {"user_id", "event_id"},
    "bingo_cells": {"user_id", "event_id"},
    "recommendation_scores": {"event_id"},
}


@pytest.fixture(scope="module")
def raw():
    return synth.generate(n_users=40, recommender_dead=False, split_started=True)


@pytest.mark.parametrize("table", sorted(REAL_COLUMNS))
def test_synth_has_the_real_columns(raw, table):
    assert REAL_COLUMNS[table] <= set(raw[table].columns), (
        f"{table} に本番の列が足りない: {REAL_COLUMNS[table] - set(raw[table].columns)}")


@pytest.mark.parametrize("table", sorted(NOT_IN_REAL_SCHEMA))
def test_synth_does_not_invent_columns(raw, table):
    """本番に無い列を合成データが持たないこと。持つと実装がそれに依存して静かに壊れる。"""
    invented = NOT_IN_REAL_SCHEMA[table] & set(raw[table].columns)
    assert not invented, f"{table} が本番に無い列を持っている: {invented}"


def test_live_tables_declaration_matches_real_schema():
    """rec_db.LIVE_TABLES が本番に無い列を SELECT しようとしていないこと。"""
    for table, forbidden in NOT_IN_REAL_SCHEMA.items():
        declared = set(rec_db.LIVE_TABLES[table])
        assert not (declared & forbidden), f"{table} で存在しない列を要求している: {declared & forbidden}"


class _InMemory:
    def __init__(self, tables):
        self._t = tables

    def table(self, name):
        return self._t[name]


def test_load_tables_resolves_user_id_from_card(raw):
    """card_id しか無いテーブルに user_id が付いて返ること。"""
    assert "user_id" not in raw["card_unlock_events"].columns  # 取得直後は無い

    out = rec_db.load_tables(_InMemory(raw))
    for name in rec_db.CARD_KEYED_TABLES:
        assert "user_id" in out[name].columns, f"{name} に user_id が付いていない"
        assert out[name]["user_id"].notna().all(), f"{name} に解決できなかった行がある"


def test_load_tables_excludes_staff(raw):
    out = rec_db.load_tables(_InMemory(raw))
    staff = set(raw["users"].loc[raw["users"]["role"] != "participant", "id"])
    assert staff, "テストデータにスタッフが居ない"
    for name, df in out.items():
        if "user_id" in df.columns:
            assert not (set(df["user_id"]) & staff), f"{name} にスタッフが残っている"


def test_scope_to_event_uses_card_derived_event_id(raw):
    """イベント絞り込みが card 由来の event_id で効くこと（users.event_id では絞らない）。"""
    out = rec_db.load_tables(_InMemory(raw), event_id=synth.EVENT_ID)
    assert len(out["card_unlock_events"]) > 0
    assert len(rec_db.load_tables(_InMemory(raw), event_id="other-event")["card_unlock_events"]) == 0


def test_unlock_events_join_to_scores_by_unlock_event_id(raw):
    """recommendation_scores.unlock_event_id が解放イベントを指していること。"""
    scores, unlocks = raw["recommendation_scores"], raw["card_unlock_events"]
    assert set(scores["unlock_event_id"]) <= set(unlocks["id"])


def test_check_ins_cell_id_points_at_cells_or_is_null(raw):
    """カード外訪問は cell_id が NULL。それ以外はマスを指す形であること。"""
    cell_ids = raw["check_ins"]["cell_id"]
    assert cell_ids.isna().any(), "カード外訪問が1件も無い（異常検知の指標が試せない）"
    assert cell_ids.notna().any(), "カード内訪問が1件も無い"


def test_forbidden_columns_never_declared():
    """users.email / password_hash を要求しないこと（02 §2）。"""
    for table, cols in rec_db.LIVE_TABLES.items():
        for col in cols:
            assert (table, col) not in rec_db.FORBIDDEN_COLUMNS
