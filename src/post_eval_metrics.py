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
