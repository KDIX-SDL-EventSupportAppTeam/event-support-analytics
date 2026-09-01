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
    assert len(out["recommendation_scores"]) > 0
    other = rec_db.load_tables(_InMemory(raw), event_id="other-event")
    assert len(other["card_unlock_events"]) == 0
    # recommendation_scores は event_id / card_id 列を持たないが、
    # unlock_event_id → card_unlock_events → bingo_cards で解決して絞る（issue #14）。
    assert len(other["recommendation_scores"]) == 0


def _two_event_tables():
    """2イベントが同居する最小の入力。scores は event_id / card_id 列を持たない。"""
    users = pd.DataFrame({"id": ["uA", "uB"], "role": ["participant", "participant"]})
    cards = pd.DataFrame({
        "id": ["card-A", "card-B"],
        "event_id": ["evt-A", "evt-B"],
        "user_id": ["uA", "uB"],
    })
    unlocks = pd.DataFrame({
        "id": ["unlock-A", "unlock-B"],
        "card_id": ["card-A", "card-B"],
        "phase": ["COVERAGE", "COVERAGE"],
        "strategy": ["RECOMMEND", "RECOMMEND"],
        "decision_table_size": [24, 24],
        "global_checkin_count": [1, 1],
        "created_at": ["2026-10-16T00:00:00Z", "2026-10-16T00:00:00Z"],
    })
    scores = pd.DataFrame({
        "id": ["score-A", "score-B"],
        "unlock_event_id": ["unlock-A", "unlock-B"],
        "user_id": ["uA", "uB"],
        "booth_id": ["b00", "b00"],
        "was_assigned": [1, 1],
        "score": [0.5, 0.5],
        "rank_in_event": [1, 1],
        "interest_match": ["MATCH", "MATCH"],
        "attributes": ["{}", "{}"],
        "reason_payload": ["{}", "{}"],
        "created_at": ["2026-10-16T00:00:00Z", "2026-10-16T00:00:00Z"],
    })
    return {
        "users": users, "bingo_cards": cards,
        "card_unlock_events": unlocks, "recommendation_scores": scores,
    }


def test_scope_to_event_filters_recommendation_scores_across_events():
    """複数イベント同居時、絞った側の scores に他イベントの行が混ざらない（issue #14）。"""
    src = _InMemory(_two_event_tables())

    scoped = rec_db.load_tables(src, ("recommendation_scores",), event_id="evt-A")
    assert list(scoped["recommendation_scores"]["id"]) == ["score-A"]
    # user_id は割れず1列のまま（attach_card_owner を流用していないこと）。
    assert "user_id_x" not in scoped["recommendation_scores"].columns
    assert list(scoped["recommendation_scores"]["user_id"]) == ["uA"]

    # event_id=None（絞り込みなし）の振る舞いは変えない。
    unscoped = rec_db.load_tables(src, ("recommendation_scores",))
    assert set(unscoped["recommendation_scores"]["id"]) == {"score-A", "score-B"}


def _scoped_len(df, event_id="evt-A"):
    """`attach_scores_event_id()` の結果を実際に絞り込みへ通し、残る行数を返す。

    「例外が出ないこと」だけを見ると、`event_id` 列を付けずに返す実装を通してしまう。
    その場合 `scope_to_event()` は全件素通しし、他イベントの行が黙って混ざる。
    **必ず絞り込みまで通して確かめる。**
    """
    return len(rec_db.scope_to_event({"recommendation_scores": df}, event_id)["recommendation_scores"])


def test_attach_scores_event_id_survives_empty_and_missing_columns():
    """空 DataFrame・列欠けで落ちず、かつ絞り込みが素通しにならないこと（02 §4 / 05 §3）。"""
    cols = list(rec_db.LIVE_TABLES["recommendation_scores"])
    cards = pd.DataFrame({"id": ["card-A"], "event_id": ["evt-A"]})
    unlocks = pd.DataFrame({"id": ["unlock-A"], "card_id": ["card-A"]})
    # 2件とも他イベント（evt-B）に属する。素通しすれば 2 件残ってしまう。
    scores = pd.DataFrame({
        "id": ["s1", "s2"],
        "unlock_event_id": ["unlock-B1", "unlock-B2"],
        "user_id": ["uB", "uB"],
    })

    # 空の入力はそもそも解決対象外。そのまま返る。
    assert rec_db.attach_scores_event_id(pd.DataFrame(columns=cols), unlocks, cards).empty

    # 相手が空でも列があるとき: 解決できず event_id は NaN → 絞り込みで落ちる。
    empty_unlocks = pd.DataFrame(columns=["id", "card_id"])
    out = rec_db.attach_scores_event_id(scores, empty_unlocks, cards)
    assert list(out.columns).count("event_id") == 1
    assert out["event_id"].isna().all()
    assert _scoped_len(out) == 0


@pytest.mark.parametrize("broken", ["unlocks", "cards"])
def test_attach_scores_event_id_does_not_pass_through_when_join_is_unusable(broken):
    """辿る先の表が使えないとき、**全件素通ししない**こと。

    列を付けずに返すと `scope_to_event()` が「event_id 列が無い表」として
    全件通し、他イベントの行が黙って混ざる（issue #14 の症状に逆戻り）。
    行単位で除外側に倒すなら表単位でも倒す（05 §3）。
    """
    cards = pd.DataFrame({"id": ["card-A"], "event_id": ["evt-A"]})
    unlocks = pd.DataFrame({"id": ["unlock-A"], "card_id": ["card-A"]})
    scores = pd.DataFrame({
        "id": ["s1", "s2"],
        "unlock_event_id": ["unlock-A", "unlock-B"],
        "user_id": ["uA", "uB"],
    })
    if broken == "unlocks":
        unlocks = unlocks[["id"]]  # card_id が欠けている
    else:
        cards = cards[["id"]]      # event_id が欠けている

    out = rec_db.attach_scores_event_id(scores, unlocks, cards)  # 例外を投げない（02 §4）
    assert "event_id" in out.columns, "event_id 列が無いと scope_to_event() が全件素通しする"
    assert out["event_id"].isna().all()
    assert len(out) == len(scores), "行を落とすのは絞り込みの仕事。ここでは落とさない"
    assert _scoped_len(out) == 0, "解決できない表は絞り込みで空になる（異常が画面で見える）"

    # event_id=None（絞り込みなし）なら全件そのまま残る。
    assert _scoped_len(out, None) == len(scores)


def test_attach_scores_event_id_is_noop_when_event_id_already_present():
    """`event_id` を既に持つ入力を割らないこと。

    merge すると `event_id_x` / `event_id_y` になり、`scope_to_event()` が
    「event_id 列が無い表」として素通しする（issue #14 の失敗がそのまま戻る）。
    ダンプ／合成データは CSV にある列をそのまま読むため、実際に起こりうる。
    """
    cards = pd.DataFrame({"id": ["card-A", "card-B"], "event_id": ["evt-A", "evt-B"]})
    unlocks = pd.DataFrame({"id": ["unlock-A", "unlock-B"], "card_id": ["card-A", "card-B"]})
    scores = pd.DataFrame({
        "id": ["s1", "s2"],
        "unlock_event_id": ["unlock-A", "unlock-B"],
        "user_id": ["uA", "uB"],
        "event_id": ["evt-A", "evt-B"],
    })

    out = rec_db.attach_scores_event_id(scores, unlocks, cards)
    assert "event_id_x" not in out.columns and "event_id_y" not in out.columns
    assert list(out.columns).count("event_id") == 1
    # 割れていなければ絞り込みが効く。
    scoped = rec_db.scope_to_event({"recommendation_scores": out}, "evt-A")
    assert list(scoped["recommendation_scores"]["id"]) == ["s1"]


def test_fetch_order_reads_referenced_tables_last():
    """参照される側（unlocks → cards）を後に読むこと。

    当日はテーブル間の取得に数秒のずれが出る（02 §4）。参照する側を先に読めば、
    後から読む参照先のほうが新しく、先に読んだ行の参照先を必ず含む。
    逆順だと、その数秒に生まれた解放イベントを指すスコアが解決できず落ちる。
    """
    order = rec_db._fetch_order({"recommendation_scores", "card_unlock_events",
                                 "bingo_cards", "check_ins", "users"})
    assert order.index("recommendation_scores") < order.index("card_unlock_events")
    assert order.index("card_unlock_events") < order.index("bingo_cards")
    assert order[-1] == "users"
    # need は set なので、明示しないと反復順が実行ごとに変わる。決定的であること。
    assert order == rec_db._fetch_order(set(order))


def test_load_tables_resolves_scores_when_unlocks_are_fetched_later():
    """scores を読んだ後に生まれた解放イベントが、後続の取得で見えれば解決できること。"""
    tables = _two_event_tables()

    class _LaggingSource:
        """scores を読んだ後に card_unlock_events へ1件増える取得口。"""

        def table(self, name):
            if name == "recommendation_scores":
                extra = tables["recommendation_scores"].iloc[[0]].copy()
                extra["id"] = ["score-late"]
                extra["unlock_event_id"] = ["unlock-LATE"]
                return pd.concat([tables["recommendation_scores"], extra], ignore_index=True)
            if name == "card_unlock_events":
                late = tables["card_unlock_events"].iloc[[0]].copy()
                late["id"] = ["unlock-LATE"]
                late["card_id"] = ["card-A"]
                return pd.concat([tables["card_unlock_events"], late], ignore_index=True)
            return tables[name]

    out = rec_db.load_tables(_LaggingSource(), ("recommendation_scores",), event_id="evt-A")
    assert set(out["recommendation_scores"]["id"]) == {"score-A", "score-late"}


def test_scope_to_event_drops_scores_that_cannot_be_resolved():
    """解決できない行は除外側に倒れること（05 §3）。card_unlock_events と同じ扱い。"""
    tables = _two_event_tables()
    ghost = tables["recommendation_scores"].iloc[[0]].copy()
    ghost["id"] = ["score-ghost"]
    ghost["unlock_event_id"] = ["unlock-MISSING"]  # card_unlock_events に無い
    tables["recommendation_scores"] = pd.concat(
        [tables["recommendation_scores"], ghost], ignore_index=True)

    out = rec_db.load_tables(_InMemory(tables), ("recommendation_scores",), event_id="evt-A")
    assert list(out["recommendation_scores"]["id"]) == ["score-A"]


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
