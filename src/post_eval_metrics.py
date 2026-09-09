"""事後の分析画面の指標。**算出式はここにだけ書く**（AGENTS.md「絶対に守ること」4）。

仕様: docs/specs/recommendation-evaluation/04-post-analysis.md

原則: 問い1つにつき決め手になる図を1つ。①②③が主役、④以降は補助。
- `interest_match` は **再計算しない**。凍結値（`recommendation_scores.interest_match`）を使う（04 §4）
- 検出力の限界（各群 600〜700枠・訪問各100件前後 → 8ポイント差まで）を図に注記する（04 §3）
- 去年データは対照群にしない。図① だけは「仕組みの変更込みで評価するのが正しい」ので去年と並べる

すべて純関数。Streamlit に依存しない。
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

import live_metrics  # noqa: E402  -- `/ops/state` の正規化を二重に持たない（03/04 共通の入れ子→フラット変換）

HIGH_RATING_DEFAULT = 4  # 「高評価」の凍結定義。星4段階で 4 以上（04 §0「事前に凍結する」）
FUNNEL_MATCH_ORDER = ["MATCH", "PARTIAL", "MISMATCH", "UNKNOWN"]
POWER_CAVEAT = "各群 600〜700枠・訪問は各群100件前後。検出できるのは 8ポイント程度の差まで。" \
               "差が出ない場合は『効果が無い』ではなく『この標本では検出できなかった』と読む。"


# --- 図① 訪問ブース数の ECDF（去年 vs 今年）（04 §2）----------------------


def ecdf(values: np.ndarray | pd.Series) -> tuple[np.ndarray, np.ndarray]:
    v = np.sort(np.asarray(values, dtype=float))
    if v.size == 0:
        return v, v
    y = np.arange(1, v.size + 1) / v.size
    return v, y


def booth_count_per_user_this_year(check_ins: pd.DataFrame) -> pd.Series:
    """今年: ユーザー別のユニーク訪問ブース数。"""
    return check_ins.groupby("user_id")["booth_id"].nunique()


def booth_count_per_user_last_year_friday(participants: pd.DataFrame) -> pd.Series:
    """去年: 金曜（代表日・192名）のユーザー別訪問ブース数（`data/tables/participants.csv`）。

    去年は「ユーザー × 日」単位。302名は2日間の延べなので、必ず金曜のみに絞る（04 §2）。
    """
    friday = pd.to_datetime("2025-10-10").date()
    p = participants.copy()
    p["day"] = pd.to_datetime(p["day"]).dt.date
    return p.loc[p["day"] == friday].set_index("pid")["n_booths"]


def booth_count_ecdf(check_ins: pd.DataFrame, last_year_participants: pd.DataFrame | None) -> dict:
    this_year = booth_count_per_user_this_year(check_ins)
    tx, ty = ecdf(this_year)
    out = {
        "this_year": {"x": tx.tolist(), "y": ty.tolist(), "median": _median(this_year), "n": int(this_year.size)},
        "note": "この図はアプリ全体の効果を示す。推薦アルゴリズムの効果とは別軸である（04 §2）。",
    }
    if last_year_participants is not None:
        ly = booth_count_per_user_last_year_friday(last_year_participants).dropna()
        lx, lyv = ecdf(ly)
        out["last_year_friday"] = {"x": lx.tolist(), "y": lyv.tolist(), "median": _median(ly), "n": int(ly.size)}
    return out


# --- 図② 参加者ごとの差のヒストグラム（実験の結論）（04 §3）--------------


def within_participant_diff(recommendation_scores: pd.DataFrame, check_ins: pd.DataFrame) -> dict:
    """参加者ひとりにつき (DRSA 枠の訪問率 − COVERAGE 枠の訪問率) を1点。

    対象は `attributes.arm` を持つ行のみ（品質ゲート通過後の解放）。**対応のある比較**。
    """
    scores = recommendation_scores.copy()
    scores["arm"] = scores["attributes"].apply(_arm_of)
    scores = scores[scores["arm"].notna()]
    if scores.empty:
        return {"diffs": [], "n_participants": 0, "mean": None, "median": None, "caveat": POWER_CAVEAT,
                "comparison": "対応のある比較（同一人物・同一時刻・同一カード内）"}

    visited = set(zip(check_ins["user_id"], check_ins["booth_id"]))
    scores["visited"] = [(u, b) in visited for u, b in zip(scores["user_id"], scores["booth_id"])]

    rate = scores.groupby(["user_id", "arm"])["visited"].mean().unstack("arm")
    rate = rate.dropna(subset=[c for c in ("DRSA", "COVERAGE") if c in rate.columns])
    if not {"DRSA", "COVERAGE"}.issubset(rate.columns) or rate.empty:
        return {"diffs": [], "n_participants": 0, "mean": None, "median": None, "caveat": POWER_CAVEAT,
                "comparison": "対応のある比較（同一人物・同一時刻・同一カード内）"}
    diff = (rate["DRSA"] - rate["COVERAGE"]).to_numpy()
    return {
        "diffs": diff.tolist(),
        "n_participants": int(diff.size),
        "mean": float(np.mean(diff)),
        "median": float(np.median(diff)),
        "caveat": POWER_CAVEAT,
        "comparison": "対応のある比較（同一人物・同一時刻・同一カード内）。違うのはアルゴリズムだけ",
    }


# --- 図③ interest_match 別のファネル（セレンディピティ）（04 §4）----------


def interest_match_funnel(recommendation_scores: pd.DataFrame, check_ins: pd.DataFrame,
                          booth_ratings: pd.DataFrame, high_rating: int = HIGH_RATING_DEFAULT) -> pd.DataFrame:
    """提示 → 訪問 → 評価 → 高評価。段ごとの件数と歩留まり（前段比）。`MISMATCH` が主役。

    `interest_match` は `recommendation_scores` の凍結値をそのまま使う（再計算しない）。
    """
    scores = recommendation_scores.copy()
    scores["interest_match"] = scores["interest_match"].fillna("UNKNOWN").astype(str)

    ci = check_ins[["user_id", "booth_id"]].drop_duplicates()
    ci["visited"] = True
    m = scores.merge(ci, on=["user_id", "booth_id"], how="left")
    m["visited"] = m["visited"].fillna(False)

    # booth_ratings は実スキーマで user_id / booth_id を直接持つ。
    # 持たない入力（古いダンプ等）のときだけ checkin_id で辿る。
    if {"user_id", "booth_id"}.issubset(booth_ratings.columns):
        rated = booth_ratings
    else:
        ci_full = check_ins[["id", "user_id", "booth_id"]].rename(columns={"id": "checkin_id"})
        rated = booth_ratings.merge(ci_full, on="checkin_id", how="inner")
    rated_pairs = rated.groupby(["user_id", "booth_id"])["rating"].max().rename("rating").reset_index()
    m = m.merge(rated_pairs, on=["user_id", "booth_id"], how="left")

    rows = []
    for label in FUNNEL_MATCH_ORDER:
        g = m[m["interest_match"] == label]
        presented = len(g)
        visited = int(g["visited"].sum())
        rated_n = int(g["rating"].notna().sum())
        high = int((g["rating"] >= high_rating).sum())
        rows.append({
            "interest_match": label,
            "presented": presented,
            "visited": visited,
            "rated": rated_n,
            "high": high,
            "visit_yield": _ratio(visited, presented),
            "rate_yield": _ratio(rated_n, visited),
            "high_yield": _ratio(high, rated_n),
        })
    return pd.DataFrame(rows)


def off_card_mismatch_rate(check_ins: pd.DataFrame, booth_category: pd.DataFrame,
                           user_interest: pd.DataFrame) -> float | None:
    """参考値: カード外訪問（`cell_id IS NULL`）で不一致カテゴリに行った率。**因果は主張しない**（04 §4）。

    booth_category: [booth_id, category]  /  user_interest: [user_id, categories(list)]
    """
    off = check_ins[check_ins["cell_id"].isna()].merge(booth_category, on="booth_id", how="left")
    off = off.merge(user_interest, on="user_id", how="left")
    if off.empty:
        return None
    def is_mismatch(row) -> bool:
        cats = row.get("categories")
        return bool(cats) and row.get("category") not in cats
    return float(off.apply(is_mismatch, axis=1).mean())


# --- 図④ 決定表件数帯別の訪問率（04 §5）--------------------------------


def visit_rate_by_decision_table_band(recommendation_scores: pd.DataFrame, check_ins: pd.DataFrame,
                                      unlock_events: pd.DataFrame, bins=(0, 30, 60, 120, 1_000_000)) -> pd.DataFrame:
    """推薦枠への訪問率を決定表件数の帯別に。**フェーズ別に色分けしない**（時刻と交絡）。記述にとどめる。"""
    ue = unlock_events[["user_id", "created_at", "decision_table_size"]].copy()
    sc = recommendation_scores[["user_id", "booth_id", "created_at"]].copy()
    ue["created_at"] = pd.to_datetime(ue["created_at"], utc=True)
    sc["created_at"] = pd.to_datetime(sc["created_at"], utc=True)
    ue = ue.sort_values("created_at")
    sc = sc.sort_values("created_at")
    merged = pd.merge_asof(sc, ue, on="created_at", by="user_id", direction="backward")
    merged["band"] = pd.cut(merged["decision_table_size"], bins=bins, right=False)

    visited = set(zip(check_ins["user_id"], check_ins["booth_id"]))
    merged["visited"] = [(u, b) in visited for u, b in zip(merged["user_id"], merged["booth_id"])]
    g = merged.groupby("band", observed=True)["visited"].agg(visit_rate="mean", n="count")
    return g.reset_index()


# --- 図⑥ was_assigned=0 との比較（04 §5）------------------------------


def assigned_vs_unassigned_scores(recommendation_scores: pd.DataFrame) -> dict:
    """推薦された候補と、されなかった候補のスコア分布。実装のサニティチェックを兼ねる。"""
    s = recommendation_scores
    a = s.loc[s["was_assigned"] == 1, "score"].dropna()
    u = s.loc[s["was_assigned"] == 0, "score"].dropna()
    return {
        "assigned": {"scores": a.tolist(), "median": _median(a), "n": int(a.size)},
        "unassigned": {"scores": u.tolist(), "median": _median(u), "n": int(u.size)},
        "sanity_ok": bool(a.median() >= u.median()) if len(a) and len(u) else None,
    }


# --- 図⑤ 規則一覧（rules_built ログが前提）（04 §5）--------------------


def rules_table(rules_built_records: list[dict], recommendation_scores: pd.DataFrame) -> pd.DataFrame:
    """`rules_built` JSONL から、その日に生成された規則の一覧。発火回数は reason_payload と突き合わせ。

    rules_built_records: JSONL の各行（dict）。各 record は少なくとも
      {"rules_built_at": iso, "rules": [{"id":.., "antecedent":{feat: op_value}, "direction":"up|down",
                                        "support":.., "confidence":..}]}
    """
    fire_counts = _rule_fire_counts(recommendation_scores)
    seen: dict[str, dict] = {}
    for rec in rules_built_records:
        built_at = pd.to_datetime(rec.get("rules_built_at"), utc=True, errors="coerce")
        for rule in rec.get("rules", []):
            rid = str(rule.get("id"))
            entry = seen.setdefault(rid, {
                "rule_id": rid,
                "rule": _format_rule(rule),
                "direction": {"up": "上方", "down": "下方"}.get(rule.get("direction"), rule.get("direction")),
                "support": rule.get("support"),
                "confidence": rule.get("confidence"),
                "first_seen": built_at,
                "last_seen": built_at,
                "fired": fire_counts.get(rid, 0),
            })
            entry["first_seen"] = min(entry["first_seen"], built_at)
            entry["last_seen"] = max(entry["last_seen"], built_at)
    return pd.DataFrame(sorted(seen.values(), key=lambda r: (-r["fired"], r["rule_id"])))


def _rule_fire_counts(recommendation_scores: pd.DataFrame) -> dict[str, int]:
    counts: dict[str, int] = {}
    for payload in recommendation_scores.get("reason_payload", pd.Series(dtype=object)):
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                continue
        if not isinstance(payload, dict):
            continue
        for rule in payload.get("rules", []):
            rid = str(rule.get("id"))
            counts[rid] = counts.get(rid, 0) + 1
    return counts


def _format_rule(rule: dict) -> str:
    ante = rule.get("antecedent", {})
    parts = [f"{k} {v}" if not isinstance(v, (int, float)) else f"{k} >= {v}" for k, v in ante.items()]
    head = " かつ ".join(parts) if parts else "（条件なし）"
    tail = "評価 >= HIGH" if rule.get("direction") == "up" else "評価 <= LOW"
    return f"if {head} then {tail}"


# --- 図⑧ エンジン状態（/ops/state の凍結値）（issue #18 / #11）----------

#: `/ops/state` の `gate_detail` の4項目。**まとめて1つの真偽値にしない**（issue #18 T-9）。
GATE_ITEMS = ("size", "rules", "gamma", "coverage")


def ops_state_summary(ops_state: dict | None) -> dict:
    """事後分析が参照する `/ops/state` 由来の値。

    当日の JSONL ログ／DB からは取れず `/ops/state` からしか取れないもの（issue #18）:

    - `gate_detail`（`size` / `rules` / `gamma` / `coverage` を**個別に**）
      — 「なぜ DRSA に上がらなかったか」の答え。来年の `PHASE_DRSA_MIN`・品質ゲート見直しの根拠
    - `decision_table_size` — 決定表が実際に何件まで育ったか
    - `snapshot_built_at` — 最後に取り込めた時刻

    取得できていない（`ops_state` が None）ときは `available=False` を返し、
    **0 や空値で埋めない**（「フェーズが上がらなかった」のか「取れていない」のかを区別する。
    issue #18「起きてはいけないこと」）。
    """
    norm = live_metrics.normalize_ops_state(ops_state)
    if norm is None:
        return {"available": False, "note": "`/ops/state` を取得できていない。"
                "『DRSA に上がらなかった』と『状態が取れていない』は区別すること（issue #18）。"}
    gate = norm.get("gate_detail") or {}
    gate_by_item = {k: gate.get(k) for k in GATE_ITEMS}
    failed = [k for k, v in gate_by_item.items() if v is False]
    passed = norm.get("quality_gate_passed")
    return {
        "available": True,
        "phase_current": norm.get("phase_current"),
        "phase_judged": norm.get("phase_judged"),
        "quality_gate_passed": passed,
        "gate_detail": gate_by_item,
        "gate_failed_items": failed,
        "gate_reason": _gate_reason(passed, failed, gate_by_item),
        "decision_table_size": norm.get("decision_table_size"),
        "snapshot_built_at": norm.get("snapshot_built_at"),
        "rules_built_at": norm.get("rules_built_at"),
        "gamma": norm.get("gamma"),
        "n_certain_rules": norm.get("n_certain_rules"),
        "rule_coverage": norm.get("rule_coverage"),
        "latency_p95_ms": norm.get("latency_p95_ms"),
    }


def _gate_reason(passed, failed: list[str], gate_by_item: dict) -> str:
    if passed:
        return "品質ゲート通過。DRSA が発火した。"
    if all(v is None for v in gate_by_item.values()):
        return "本番推薦を1件も処理していないため gate_detail が未提供（null）。0/false で埋めない。"
    if failed:
        label = {"size": "決定表件数", "rules": "確実規則の本数", "gamma": "近似の質 γ",
                 "coverage": "規則の被覆率"}
        return "品質ゲート未通過。落ちた項目: " + "、".join(label.get(k, k) for k in failed) \
            + "（この項目が来年の見直し対象）。"
    return "品質ゲート未通過（落ちた項目の内訳は gate_detail 参照）。"


# --- 図⑨ 推薦パラメータの妥当性検証（issue #11）------------------------
#
# 推薦側 ADR 0009「当日は既定値のまま走らせ、調整は事後に行う」の「事後」を引き受ける。
# **フェーズは時刻と交絡する**（01 §2）。フェーズ別の比較で因果を主張しない。記述にとどめる。
# しきい値を決めるのは人間である（推薦側 03-phases.md §3.1）。ここは材料を出すだけ。

#: 推薦側 03-phases.md §3 の既定値。**根拠が弱い／未決定**のものを含む（issue #11 の検証対象）。
PHASE_SIMILARITY_MIN_DEFAULT = 30   # 「根拠が弱いと明記されている」（03-phases.md §3.2）
PHASE_DRSA_MIN_DEFAULT = 60         # 条件属性2個での既定。3個なら 180（§3.1）
DRSA_MIN_RULES_DEFAULT = 3
DRSA_MIN_GAMMA_DEFAULT = 0.5
DRSA_MIN_COVERAGE_DEFAULT = 0.5
PHASE_ORDER = ("COVERAGE", "SIMILARITY", "DRSA")

#: 感度分析の既定シナリオ。(PHASE_SIMILARITY_MIN, PHASE_DRSA_MIN)。
COUNTERFACTUAL_SCENARIOS = {
    "既定（30 / 60）": (30, 60),
    "DRSA_MIN=180（条件属性3個相当）": (30, 180),
    "SIMILARITY_MIN=20": (20, 60),
    "SIMILARITY_MIN=45": (45, 60),
    "DRSA_MIN=45": (30, 45),
}


def phase_from_size(size, similarity_min: int = PHASE_SIMILARITY_MIN_DEFAULT,
                    drsa_min: int = PHASE_DRSA_MIN_DEFAULT) -> str | None:
    """`decision_table_size` からフェーズを引く（件数条件のみ。品質ゲートは別。03-phases.md §1）。

    測れなかった（`null`）ものは `None` を返す。**0 と区別する**（§2）。
    """
    if size is None or (isinstance(size, float) and np.isnan(size)):
        return None
    size = float(size)
    if size < similarity_min:
        return "COVERAGE"
    if size < drsa_min:
        return "SIMILARITY"
    return "DRSA"


def _rec_visit_rate(scores_subset: pd.DataFrame, check_ins: pd.DataFrame) -> float | None:
    """推薦枠（`was_assigned=1`）のうち実際に訪問された割合。"""
    assigned = scores_subset[scores_subset["was_assigned"] == 1]
    if assigned.empty:
        return None
    visited = set(zip(check_ins["user_id"], check_ins["booth_id"]))
    hit = [(u, b) in visited for u, b in zip(assigned["user_id"], assigned["booth_id"])]
    return float(np.mean(hit))


def phase_comparison(unlock_events: pd.DataFrame, recommendation_scores: pd.DataFrame,
                     check_ins: pd.DataFrame) -> pd.DataFrame:
    """**実際に使われた**フェーズ（`card_unlock_events.phase`）別の記述比較。

    `card_unlock_events.phase` にはその解放で実際に使われた戦略が入る（issue #11）。
    フェーズは来場時刻と交絡するため（早い人ほど COVERAGE、遅い人ほど DRSA）、
    **群間差を効果として読まない**（01 §2・04 §5）。列は記述統計にとどめる。
    """
    ue = unlock_events.copy()
    ue["created_at"] = pd.to_datetime(ue["created_at"], utc=True)
    sc = recommendation_scores.copy()
    sc["created_at"] = pd.to_datetime(sc["created_at"], utc=True)
    # scores を解放イベントへ結び付ける（unlock_event_id があればそれで、無ければ時刻で）
    if "unlock_event_id" in sc.columns and "id" in ue.columns:
        link = sc.merge(ue[["id", "phase"]].rename(columns={"id": "unlock_event_id"}),
                        on="unlock_event_id", how="left")
    else:
        link = pd.merge_asof(sc.sort_values("created_at"),
                             ue[["created_at", "user_id", "phase"]].sort_values("created_at"),
                             on="created_at", by="user_id", direction="backward")
    rows = []
    for phase in PHASE_ORDER:
        u = ue[ue["phase"] == phase]
        s = link[link["phase"] == phase]
        sizes = u["decision_table_size"].dropna()
        rows.append({
            "phase": phase,
            "n_unlocks": int(len(u)),
            "n_users": int(u["user_id"].nunique()) if "user_id" in u.columns else None,
            "decision_table_size_min": int(sizes.min()) if not sizes.empty else None,
            "decision_table_size_max": int(sizes.max()) if not sizes.empty else None,
            "fallback_rate": float((u["strategy"] == "FALLBACK_COVERAGE").mean()) if len(u) else None,
            "rec_slot_visit_rate": _rec_visit_rate(s, check_ins),
            "first_seen_at": u["created_at"].min() if len(u) else pd.NaT,
        })
    out = pd.DataFrame(rows)
    out.attrs["caveat"] = "フェーズは来場時刻と交絡する。群間差を効果として読まない（01 §2）。"
    return out


def phase_change_times(unlock_events: pd.DataFrame,
                       phase_changed_records: list[dict] | None = None) -> pd.DataFrame:
    """フェーズが切り替わった時刻。列: `at` / `from` / `to` / `judged_phase` / `fallback_reason` / `source`。

    推薦側の `phase_changed` ログ（`kind == "phase_changed"`）があればそれを使う。
    無ければ DB（`card_unlock_events.phase` の変化）から復元する。**DB 由来は当日必ず取れる。**
    デモ・リプレイ由来（`log_kind != "recommend"`）は研究ログではないので除外する（推薦側 10 §3）。
    """
    cols = ["at", "from", "to", "judged_phase", "fallback_reason", "source"]
    recs = [r for r in (phase_changed_records or [])
            if r.get("kind", "phase_changed") == "phase_changed"
            and r.get("log_kind", "recommend") == "recommend"]
    if recs:
        df = pd.DataFrame([{
            "at": r.get("ts"),
            "from": r.get("from"), "to": r.get("to"),
            "judged_phase": r.get("judged_phase"),
            "fallback_reason": r.get("fallback_reason"),
            "source": "log(phase_changed)",
        } for r in recs])
        df["at"] = pd.to_datetime(df["at"], utc=True, errors="coerce")
        return df.sort_values("at").reset_index(drop=True)[cols]

    if unlock_events.empty:
        return pd.DataFrame(columns=cols)
    u = unlock_events.copy()
    u["created_at"] = pd.to_datetime(u["created_at"], utc=True)
    u = u.sort_values("created_at").reset_index(drop=True)
    prev_phase = u["phase"].shift()
    changed = u["phase"].ne(prev_phase)
    changed.iloc[0] = False  # 最初の解放は「切り替わり」ではない
    # `.values` は tz-aware Series を naive な numpy datetime64 に落とす。
    # Series のまま抜き出して index を振り直すことで datetime64[..., UTC] を保つ。
    df = pd.DataFrame({
        "at": u.loc[changed, "created_at"].reset_index(drop=True),
        "from": prev_phase[changed].reset_index(drop=True),
        "to": u.loc[changed, "phase"].reset_index(drop=True),
    })
    df["at"] = pd.to_datetime(df["at"], utc=True)  # 空でも tz-aware dtype を保証する
    df["judged_phase"] = None
    df["fallback_reason"] = None
    df["source"] = "db(card_unlock_events)"
    return df[cols]


def counterfactual_phase_distribution(
        unlock_events: pd.DataFrame,
        scenarios: dict[str, tuple[int, int]] | None = None) -> pd.DataFrame:
    """`decision_table_size` から「別のしきい値ならどのフェーズだったか」を再計算する（issue #11）。

    件数条件のみの再計算である（品質ゲートは `/ops/state` 側。§ ゲート感度は別関数）。
    先頭行 `実測` は `card_unlock_events.phase`（実際に使われた値）。
    """
    scenarios = scenarios or COUNTERFACTUAL_SCENARIOS
    sizes = unlock_events["decision_table_size"]
    n_total = int(len(unlock_events))
    n_measured = int(sizes.notna().sum())

    def _dist(series: pd.Series) -> dict:
        counts = series.value_counts()
        drsa_users = unlock_events.loc[series[series == "DRSA"].index, "user_id"].nunique() \
            if "user_id" in unlock_events.columns else None
        return {
            **{p: int(counts.get(p, 0)) for p in PHASE_ORDER},
            "未測定(null)": int(series.isna().sum()),
            "DRSA到達 解放数": int(counts.get("DRSA", 0)),
            "DRSA到達 参加者数": int(drsa_users) if drsa_users is not None else None,
        }

    rows = [{"scenario": "実測（card_unlock_events.phase）", "PHASE_SIMILARITY_MIN": None,
             "PHASE_DRSA_MIN": None, **_dist(unlock_events["phase"])}]
    for name, (smin, dmin) in scenarios.items():
        recomputed = sizes.map(lambda v: phase_from_size(v, smin, dmin))
        rows.append({"scenario": name, "PHASE_SIMILARITY_MIN": smin, "PHASE_DRSA_MIN": dmin,
                     **_dist(recomputed)})
    out = pd.DataFrame(rows)
    out.attrs["n_total"] = n_total
    out.attrs["n_measured"] = n_measured
    return out


def threshold_report(unlock_events: pd.DataFrame, ops_state: dict | None = None, *,
                     similarity_min: int = PHASE_SIMILARITY_MIN_DEFAULT,
                     drsa_min: int = PHASE_DRSA_MIN_DEFAULT,
                     min_rules: int = DRSA_MIN_RULES_DEFAULT,
                     min_gamma: float = DRSA_MIN_GAMMA_DEFAULT,
                     min_coverage: float = DRSA_MIN_COVERAGE_DEFAULT) -> dict:
    """各しきい値に到達したかを記録する。

    **到達しなかったこと自体は失敗ではない**（issue #11）。「未到達」も結果として残す。
    規則が出ないからといってゲートを下げるのは去年の失敗の再現（推薦側 03-phases.md §3.3・R-3）。
    """
    if "decision_table_size" in unlock_events.columns:
        sizes = pd.to_numeric(unlock_events["decision_table_size"], errors="coerce").dropna()
    else:
        sizes = pd.Series(dtype="float64")
    max_size = int(sizes.max()) if not sizes.empty else None
    drsa_ever = bool((unlock_events["phase"] == "DRSA").any()) if "phase" in unlock_events.columns else False

    checks = [
        _check("PHASE_SIMILARITY_MIN", similarity_min, max_size,
               None if max_size is None else max_size >= similarity_min,
               "決定表件数の最大値がしきい値に届いたか"),
        _check("PHASE_DRSA_MIN（件数条件）", drsa_min, max_size,
               None if max_size is None else max_size >= drsa_min,
               "件数だけで見た DRSA 到達可否。品質ゲートは別"),
    ]

    gate = ops_state_summary(ops_state)
    if not gate["available"]:
        checks.append({"param": "品質ゲート（DRSA_MIN_RULES/GAMMA/COVERAGE）", "threshold": None,
                       "observed": None, "reached": None,
                       "note": "/ops/state 未取得のため判定不能。当日の ops_state.json を置くこと"})
    else:
        g_gamma, g_rules, g_cov = gate["gamma"], gate["n_certain_rules"], gate["rule_coverage"]
        checks += [
            _check("DRSA_MIN_RULES", min_rules, g_rules,
                   None if g_rules is None else g_rules >= min_rules,
                   "確実規則の本数（/ops/state のスナップショット時点。全期間の最大ではない）"),
            _check("DRSA_MIN_GAMMA", min_gamma, g_gamma,
                   None if g_gamma is None else g_gamma >= min_gamma,
                   "近似の質 γ（同上・時点値）"),
            _check("DRSA_MIN_COVERAGE", min_coverage, g_cov,
                   None if g_cov is None else g_cov >= min_coverage,
                   "規則が候補を覆う割合（同上・時点値）"),
        ]
        checks.append({"param": "品質ゲート 総合（gate_detail）", "threshold": "全項目 AND",
                       "observed": "通過" if gate["quality_gate_passed"] else
                       ("未提供" if gate["quality_gate_passed"] is None else
                        "未通過: " + ", ".join(gate["gate_failed_items"])),
                       "reached": gate["quality_gate_passed"], "note": gate["gate_reason"]})

    reached = [c["param"] for c in checks if c["reached"] is True]
    not_reached = [c["param"] for c in checks if c["reached"] is False]
    undetermined = [c["param"] for c in checks if c["reached"] is None]
    return {
        "max_decision_table_size": max_size,
        "drsa_phase_ever_used": drsa_ever,
        "checks": checks,
        "reached": reached,
        "not_reached": not_reached,
        "undetermined": undetermined,
        "summary": _threshold_summary(max_size, drsa_ever, not_reached, undetermined),
    }


def _check(param: str, threshold, observed, reached, note: str) -> dict:
    return {"param": param, "threshold": threshold, "observed": observed,
            "reached": reached, "note": note}


def _threshold_summary(max_size, drsa_ever: bool, not_reached: list[str], undetermined: list[str]) -> str:
    parts = []
    if max_size is None:
        parts.append("決定表件数が1件も測れていない（`null`）。当日エンジンがデータへ到達できていなかった可能性。")
    else:
        parts.append(f"決定表件数の最大は {max_size}。")
    parts.append("DRSA フェーズは当日" + ("使われた。" if drsa_ever else "一度も使われなかった。"))
    if not_reached:
        parts.append("未到達（失敗ではなく、その事実を結果として記録する）: " + " / ".join(not_reached) + "。")
    if undetermined:
        parts.append("判定不能（データ不足）: " + " / ".join(undetermined) + "。")
    if not not_reached and not undetermined:
        parts.append("すべてのしきい値に到達した。")
    return " ".join(parts)


# --- 図⑦ 個票ビュー（1人の物語）（04 §6）------------------------------


def participant_timeline(user_id: str, check_ins: pd.DataFrame, recommendation_scores: pd.DataFrame,
                         unlock_events: pd.DataFrame, booth_ratings: pd.DataFrame) -> list[dict]:
    """1人ぶんの出来事を時系列に並べる。仮名 ID のまま。実名・メールは扱わない（04 §6）。"""
    events: list[tuple[pd.Timestamp, str, str]] = []

    ci = check_ins[check_ins["user_id"] == user_id]
    rating_by_checkin = booth_ratings.set_index("checkin_id")["rating"].to_dict()
    for _, r in ci.iterrows():
        rating = rating_by_checkin.get(r.get("id"))
        cell = "カード外" if pd.isna(r["cell_id"]) else f"マス{r['cell_id']}"
        rtxt = f" 評価{int(rating)}" if pd.notna(rating) else ""
        events.append((pd.to_datetime(r["checked_in_at"], utc=True), "checkin",
                       f"チェックイン booth={r['booth_id']} {cell}{rtxt}"))

    for _, u in unlock_events[unlock_events["user_id"] == user_id].iterrows():
        events.append((pd.to_datetime(u["created_at"], utc=True), "unlock",
                       f"【解放】phase={u['phase']} strategy={u['strategy']} table={u['decision_table_size']}"))

    sc = recommendation_scores[recommendation_scores["user_id"] == user_id]
    for _, s in sc.iterrows():
        arm = _arm_of(s["attributes"])
        armtxt = f" [{arm}]" if arm else ""
        events.append((pd.to_datetime(s["created_at"], utc=True), "score",
                       f"提示{armtxt} booth={s['booth_id']} score={s['score']} {s['interest_match']}"))

    events.sort(key=lambda e: (e[0] is pd.NaT, e[0]))
    return [{"at": at, "kind": k, "text": t} for at, k, t in events]


# --- 補助 -----------------------------------------------------------------


def _median(s: pd.Series) -> float | None:
    s = pd.Series(s).dropna()
    return float(s.median()) if len(s) else None


def _ratio(num: int, den: int) -> float | None:
    return float(num / den) if den else None


def _arm_of(attributes) -> str | None:
    if isinstance(attributes, str):
        try:
            attributes = json.loads(attributes)
        except (ValueError, TypeError):
            return None
    if isinstance(attributes, dict):
        arm = attributes.get("arm")
        return str(arm) if arm is not None else None
    return None
