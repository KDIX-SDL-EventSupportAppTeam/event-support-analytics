"""完全ダンプから、エンジニア・研究者向けの「推薦評価 研究データ」xlsx を作る。

使い方（event-support-analytics で）:
    .venv/bin/python explorer/export_research_xlsx.py ../db-archive/2026-10-16-prod-event/full

方針（docs/specs/recommendation-evaluation と event-support-recommend 09-research-design.md に合わせる）
- 除外: role = 'participant' 以外（許可リスト）。チェックイン0件・カード未生成の参加者は残す
- 個人: 名前・メールは出さない。仮名 ID（P0001…）のみ。対応表は作らない
- 指標の定義は事前登録どおり: 主要 = 推薦枠への訪問（解放時刻以降に同じ参加者が同じブースへチェックイン）、
  高評価 = ★4、セレンディピティ = interest_match=MISMATCH かつ訪問かつ★4。interest_match は推薦時の凍結値を使い再計算しない
- 時刻: すべて JST に揃える。UTC 保存の列（check_ins.checked_in_at / bingo_cells.* / gacha_coin_uses.used_at）だけ +9h
  （2026-10-19 実測: rated_at − checked_in_at の中央値が +540 分 → 補正後は数秒）
- 加工は「結合・分類・数え上げ」まで。統計検定はしない（ANALYSIS.md の scripts に任せる）
- 値は標準出力に出さない。出すのはシート名・行数・照合用の件数だけ
"""

import json
import os
import sys
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq
from openpyxl import Workbook
from openpyxl.formatting.rule import DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

UTC_COLUMNS = {("check_ins", "checked_in_at"), ("bingo_cells", "assigned_at"), ("bingo_cells", "achieved_at"),
               ("gacha_coin_uses", "used_at")}
DAY0 = datetime(2026, 10, 16)          # JST。これより前はリハーサル（is_event_day=0）
ARM = {"RANDOM": "RANDOM", "DRSA": "DRSA系", "SER": "DRSA系", "EXPLORE": "EXPLORE", "PRESURVEY": "PRESURVEY",
       "FALLBACK_COVERAGE": "FALLBACK"}
STRATEGY_JA = {"RANDOM": "品質ゲート不通過期。候補ごとに一様乱数", "DRSA": "ラフ集合（DRSA）の規則でスコア付け",
               "SER": "セレンディピティ規則が選んだ関心外ブース", "EXPLORE": "関心外枠を探索で選んだもの",
               "PRESURVEY": "事前アンケートから選んだ最初の1マス"}
HEAD = (PatternFill("solid", fgColor="1F2937"), Font(bold=True, color="FFFFFF"))


def J(s):
    try:
        return json.loads(s) if s else {}
    except ValueError:
        return {}


def num(v, f=float):
    try:
        return f(v) if v not in (None, "") else None
    except ValueError:
        return None


class D:
    def __init__(self, dump: Path):
        m = json.loads((dump / "manifest.json").read_text(encoding="utf-8"))
        self.m = m
        self.t = {n: pq.read_table(dump / i["file"]).to_pylist() for n, i in m["tables"].items()}

    def ts(self, table, col, v):
        if not v:
            return None
        d = datetime.fromisoformat(str(v).replace("Z", ""))
        return d + timedelta(hours=9) if (table, col) in UTC_COLUMNS else d


def build(d: D):
    t = d.t
    role = {u["id"]: u["role"] for u in t["users"]}
    keep = lambda uid: role.get(uid) == "participant"
    booths = {b["id"]: b for b in t["booths"]}
    cats = {c["id"]: c["name"] for c in t["categories"]}
    bcode = lambda bid: f"B{int(booths[bid]['display_code']):02d}" if bid in booths else None
    bname = lambda bid: booths.get(bid, {}).get("name")
    bcat = lambda bid: cats.get(booths.get(bid, {}).get("category_id"))
    card_owner = {c["id"]: c["user_id"] for c in t["bingo_cards"]}

    # ---- 参加者の仮名 ID（最初の行動順） ----
    ci = [c for c in t["check_ins"] if keep(c["user_id"])]
    for c in ci:
        c["_t"] = d.ts("check_ins", "checked_in_at", c["checked_in_at"])
    first = {}
    for c in ci:
        first[c["user_id"]] = min(first.get(c["user_id"], c["_t"]), c["_t"])
    users = [u for u in t["users"] if keep(u["id"])]
    users.sort(key=lambda u: (first.get(u["id"]) or datetime.max, u.get("created_at") or "", u["id"]))
    pid = {u["id"]: f"P{i:04d}" for i, u in enumerate(users, 1)}

    survey = {}
    for s in t["user_survey_answers"]:
        if keep(s["user_id"]):
            a = J(s["custom_answers"])
            for k in ("age_range", "occupation", "industry"):
                if s.get(k) and k not in a:
                    a[k] = s[k]
            survey[s["user_id"]] = a
    interests = {u: set(a.get("interest_categories") or []) for u, a in survey.items()}

    # ---- 評価・訪問の索引 ----
    rating = {r["checkin_id"]: r for r in t["booth_ratings"]}
    for r in rating.values():
        r["_t"] = d.ts("booth_ratings", "rated_at", r["rated_at"])
    visits = defaultdict(list)        # (user, booth) -> 時刻順の check-in
    by_user = defaultdict(list)
    for c in sorted(ci, key=lambda c: c["_t"]):
        visits[(c["user_id"], c["booth_id"])].append(c)
        by_user[c["user_id"]].append(c)
    end_t = max(c["_t"] for c in ci)
    cells = {c["id"]: c for c in t["bingo_cells"]}
    cell_of = defaultdict(list)       # (user, booth) -> マス
    for c in t["bingo_cells"]:
        u = card_owner.get(c["card_id"])
        if u and c["booth_id"]:
            cell_of[(u, c["booth_id"])].append(c)

    # ---- 解放イベント ----
    unlocks = [u for u in t["card_unlock_events"] if keep(card_owner.get(u["card_id"]))]
    for u in unlocks:
        u["_t"] = d.ts("card_unlock_events", "created_at", u["created_at"])
        u["_user"] = card_owner[u["card_id"]]
    unlocks.sort(key=lambda u: u["_t"])
    uid_of = {u["id"]: f"U{i:05d}" for i, u in enumerate(unlocks, 1)}
    unlock = {u["id"]: u for u in unlocks}
    n_by_card = Counter()
    for u in unlocks:
        if u["phase"] != "PRESURVEY":
            n_by_card[u["card_id"]] += 1
            u["_no"] = n_by_card[u["card_id"]]
        else:
            u["_no"] = 0

    # ---- 推薦スコア（候補全件）と推薦枠 ----
    cand = [r for r in t["recommendation_scores"] if keep(r["user_id"]) and r["unlock_event_id"] in unlock]
    for r in cand:
        r["_rp"], r["_at"] = J(r["reason_payload"]), J(r["attributes"])
        u = unlock[r["unlock_event_id"]]
        # strategy が無い枠: 事前アンケート枠は PRESURVEY、フォールバック解放の枠は解放側の種類（FALLBACK_COVERAGE）
        r["_strategy"] = r["_rp"].get("strategy") or ("PRESURVEY" if u["phase"] == "PRESURVEY" else u["strategy"] or "不明")
    slots = [r for r in cand if r["was_assigned"] == "1"]
    slots.sort(key=lambda r: (unlock[r["unlock_event_id"]]["_t"], int(r["rank_in_event"] or 0)))
    day_drsa = [unlock[r["unlock_event_id"]]["_t"] for r in slots if r["_strategy"] == "DRSA" and unlock[r["unlock_event_id"]]["_t"] >= DAY0]
    drsa_start, drsa_end = (min(day_drsa), max(day_drsa)) if day_drsa else (None, None)

    def period(tm):
        if not drsa_start:
            return ""
        return "P1_RANDOM_early" if tm < drsa_start else "P2_DRSA" if tm <= drsa_end else "P3_RANDOM_late"

    import re as _re
    def readable_ids(text):
        return _re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                       lambda m_: cats.get(m_.group(0)) or bcode(m_.group(0)) or "（ID）", text)

    rec_rows, rec_by_user_booth = [], defaultdict(list)
    for i, r in enumerate(slots, 1):
        u = unlock[r["unlock_event_id"]]; who, bid, tu = r["user_id"], r["booth_id"], u["_t"]
        vs = visits.get((who, bid), [])
        after = [c for c in vs if c["_t"] >= tu]
        hit = after[0] if after else None
        rt = rating.get(hit["id"]) if hit else None
        ttv = (hit["_t"] - tu).total_seconds() / 60 if hit else None
        raw = r["_at"].get("raw", {})
        rules = r["_rp"].get("rules") or []
        cell = (cell_of.get((who, bid)) or [None])[0]
        star = num(rt["rating"], int) if rt else None
        im = r["interest_match"]
        row = {
            "slot_id": f"R{i:05d}", "participant_id": pid[who], "unlock_id": uid_of[u["id"]], "unlock_no": u["_no"],
            "unlock_at_jst": tu, "is_event_day": int(tu >= DAY0), "unlock_phase": u["phase"], "unlock_strategy": u["strategy"],
            "decision_table_size": num(u["decision_table_size"], int), "global_checkin_count": num(u["global_checkin_count"], int),
            "strategy": r["_strategy"], "arm": ARM.get(r["_strategy"], r["_strategy"]), "period": period(tu) if tu >= DAY0 else "rehearsal",
            "slot": r["_rp"].get("slot") if isinstance(r["_rp"].get("slot"), str) else None, "scored_by": r["_rp"].get("scored_by"),
            "booth_code": bcode(bid), "booth_name": bname(bid), "booth_category": bcat(bid),
            "interest_match": im, "declared": raw.get("declared"), "behavioral": raw.get("behavioral"),
            "score": num(r["score"]), "rank_in_event": num(r["rank_in_event"], int), "visitor_count_at_rec": raw.get("visitor_count"),
            "matched_rules": raw.get("matched_rules"), "rule_ids": ",".join(x.get("id", "") for x in rules), "ser_rule": readable_ids(json.dumps(r["_rp"]["ser_rule"], ensure_ascii=False)) if r["_rp"].get("ser_rule") else None,
            "card_position": num(cell["position"], int) if cell else None, "card_zone": cell["zone"] if cell else None, "card_source": cell["source"] if cell else None,
            "visited_before_unlock": int(any(c["_t"] < tu for c in vs)), "prior_checkins": bisect_left([c["_t"] for c in by_user[who]], tu),
            "visit": int(bool(hit)), "visit_at_jst": hit["_t"] if hit else None, "minutes_to_visit": round(ttv, 1) if ttv is not None else None,
            "visit30": int(bool(hit) and ttv <= 30), "exposure_min": round((end_t - tu).total_seconds() / 60, 1),
            "rated": int(bool(rt)), "rating": star, "star4": int(star == 4), "visit_star4": int(bool(hit) and star == 4),
            "has_comment": int(bool(rt and (rt["comment"] or "").strip())),
            "serendipity": int(im == "MISMATCH" and bool(hit) and star == 4),
        }
        rec_rows.append(row)
        rec_by_user_booth[(who, bid)].append((tu, row))

    # ---- 候補全件（was_assigned=0 との比較用） ----
    cand_rows = []
    for i, r in enumerate(sorted(cand, key=lambda r: (unlock[r["unlock_event_id"]]["_t"], num(r["rank_in_event"], int) or 0)), 1):
        u = unlock[r["unlock_event_id"]]
        cand_rows.append({"candidate_id": f"C{i:06d}", "unlock_id": uid_of[u["id"]], "participant_id": pid[r["user_id"]],
                          "unlock_at_jst": u["_t"], "booth_code": bcode(r["booth_id"]), "was_assigned": int(r["was_assigned"] == "1"),
                          "strategy": r["_strategy"], "scored_by": r["_rp"].get("scored_by"), "score": num(r["score"]),
                          "rank_in_event": num(r["rank_in_event"], int), "interest_match": r["interest_match"],
                          "matched_rules": r["_at"].get("raw", {}).get("matched_rules"),
                          "visited_later": int(any(c["_t"] >= u["_t"] for c in visits.get((r["user_id"], r["booth_id"]), [])))})

    # ---- 解放イベント ----
    slots_by_unlock = defaultdict(list)
    for r in rec_rows:
        slots_by_unlock[r["unlock_id"]].append(r)
    unlock_rows = [{"unlock_id": uid_of[u["id"]], "participant_id": pid[u["_user"]], "unlock_no": u["_no"], "unlock_at_jst": u["_t"],
                    "is_event_day": int(u["_t"] >= DAY0), "phase": u["phase"], "strategy": u["strategy"], "line_index": num(u["line_index"], int),
                    "released_positions": u["released_positions"], "decision_table_size": num(u["decision_table_size"], int),
                    "global_checkin_count": num(u["global_checkin_count"], int),
                    "slots": len(slots_by_unlock[uid_of[u["id"]]]), "slots_visited": sum(x["visit"] for x in slots_by_unlock[uid_of[u["id"]]]),
                    "strategies": ",".join(sorted({x["strategy"] for x in slots_by_unlock[uid_of[u["id"]]]}))} for u in unlocks]

    # ---- チェックイン ----
    ci_rows = []
    for who in pid:
        for k, c in enumerate(by_user[who], 1):
            rt = rating.get(c["id"]); cell = cells.get(c["cell_id"]) if c["cell_id"] else None
            prior_recs = [row for tu, row in rec_by_user_booth.get((who, c["booth_id"]), []) if tu <= c["_t"]]
            bc = booths.get(c["booth_id"], {}).get("category_id")
            ci_rows.append({
                "participant_id": pid[who], "visit_seq": k, "visit_order_db": num(c["visit_order"], int), "checked_in_at_jst": c["_t"],
                "is_event_day": int(c["_t"] >= DAY0), "booth_code": bcode(c["booth_id"]), "booth_name": bname(c["booth_id"]),
                "booth_category": bcat(c["booth_id"]), "checkin_method": c["checkin_method"],
                "via_card_cell": int(bool(cell)), "card_position": num(cell["position"], int) if cell else None,
                "card_source": cell["source"] if cell else None,
                "was_recommended_before": int(bool(prior_recs)), "rec_strategy": prior_recs[-1]["strategy"] if prior_recs else None,
                "rec_interest_match": prior_recs[-1]["interest_match"] if prior_recs else None,
                "interest_category_match_posthoc": (int(bc in interests[who]) if who in interests else None),
                "rated": int(bool(rt)), "rating": num(rt["rating"], int) if rt else None, "star4": int(bool(rt) and rt["rating"] == "4"),
                "has_comment": int(bool(rt and (rt["comment"] or "").strip())), "rating_prompt": rt["prompt_context"] if rt else None,
                "seconds_to_rate": round((rt["_t"] - c["_t"]).total_seconds()) if rt else None,
                "repeat_visit": int(k > 1 and any(x["booth_id"] == c["booth_id"] for x in by_user[who][:k - 1])),
            })

    # ---- 参加者 ----
    votes = Counter(v["user_id"] for v in t["award_votes"])
    gacha = Counter(g["user_id"] for g in t["gacha_coin_uses"])
    achieved = Counter(card_owner.get(c["card_id"]) for c in t["bingo_cells"] if c["is_achieved"] == "1")
    recs_by_pid = defaultdict(list)
    for r in rec_rows:
        recs_by_pid[r["participant_id"]].append(r)
    p_rows = []
    for u in users:
        who = u["id"]; a = survey.get(who, {}); vs = [c for c in by_user[who] if c["_t"] >= DAY0]
        day = [r for r in recs_by_pid[pid[who]] if r["is_event_day"] and r["strategy"] in ("RANDOM", "DRSA", "SER", "EXPLORE")]
        arms = {r["arm"] for r in day}
        row = {"participant_id": pid[who], "survey_answered": int(who in survey), "gender": a.get("gender"), "age_range": a.get("age_range"),
               "occupation": ",".join(a["occupation"]) if isinstance(a.get("occupation"), list) else a.get("occupation"),
               "affiliation": a.get("affiliation"), "exploration_disposition": a.get("exploration_disposition"),
               "interest_categories": ",".join(cats.get(x, x) for x in a.get("interest_categories") or []),
               "top_interest_category": cats.get(a.get("top_interest_category"), a.get("top_interest_category")),
               "first_checkin_jst": vs[0]["_t"] if vs else None, "last_checkin_jst": vs[-1]["_t"] if vs else None,
               "stay_min": round((vs[-1]["_t"] - vs[0]["_t"]).total_seconds() / 60, 1) if vs else None,
               "checkins": len(vs), "unique_booths": len({c["booth_id"] for c in vs}),
               "offcard_checkins": sum(1 for c in vs if not c["cell_id"]),
               "ratings": sum(1 for c in vs if c["id"] in rating), "star4": sum(1 for c in vs if rating.get(c["id"], {}).get("rating") == "4"),
               "unlocks": sum(1 for x in unlocks if x["_user"] == who and x["phase"] != "PRESURVEY"),
               "rec_slots": len(day), "rec_visits": sum(r["visit"] for r in day),
               "arm_mix": "両方" if {"RANDOM", "DRSA系"} <= arms else "DRSA系のみ" if "DRSA系" in arms else "RANDOMのみ" if "RANDOM" in arms else ("EXPLOREのみ" if arms else "推薦なし"),
               "bingo_achieved_cells": achieved.get(who, 0), "gacha_coins_used": gacha.get(who, 0), "award_votes": votes.get(who, 0)}
        for s in ("RANDOM", "DRSA", "SER", "EXPLORE"):
            x = [r for r in day if r["strategy"] == s]
            row[f"slots_{s}"] = len(x); row[f"visits_{s}"] = sum(r["visit"] for r in x)
        p_rows.append(row)

    # ---- ブース ----
    b_rows = []
    # 主要分析の母集団: 当日・PRESURVEY とフォールバック枠を除く（ANALYSIS.md と同じ 1,474 枠）
    day_rec = [r for r in rec_rows if r["is_event_day"] and r["strategy"] in ("RANDOM", "DRSA", "SER", "EXPLORE")]
    for b in sorted(t["booths"], key=lambda b: int(b["display_code"] or 0)):
        bid = b["id"]; code = bcode(bid)
        bc = [c for c in ci_rows if c["booth_code"] == code and c["is_event_day"]]
        rr = [c["rating"] for c in bc if c["rating"]]
        row = {"booth_code": code, "booth_name": b["name"], "booth_category": bcat(bid), "is_active": b["is_active"],
               "checkins": len(bc), "unique_visitors": len({c["participant_id"] for c in bc}),
               "offcard_checkins": sum(1 for c in bc if not c["via_card_cell"]),
               "ratings": len(rr), "mean_rating": round(sum(rr) / len(rr), 3) if rr else None, "star4_rate": round(sum(1 for x in rr if x == 4) / len(rr), 3) if rr else None,
               "candidate_count": sum(1 for c in cand_rows if c["booth_code"] == code), "award_votes": sum(1 for v in t["award_votes"] if v["booth_id"] == bid and keep(v["user_id"]))}
        for s in ("RANDOM", "DRSA", "SER", "EXPLORE"):
            x = [r for r in day_rec if r["booth_code"] == code and r["strategy"] == s]
            row[f"slots_{s}"] = len(x); row[f"visits_{s}"] = sum(r["visit"] for r in x)
        b_rows.append(row)

    # ---- 集計（記述のみ） ----
    def funnel(rows, key):
        g = defaultdict(list)
        for r in rows:
            g[key(r)].append(r)
        out = []
        for k, rs in sorted(g.items(), key=lambda x: str(x[0])):
            n = len(rs); v = sum(r["visit"] for r in rs); rated = sum(r["rated"] for r in rs if r["visit"]); s4 = sum(r["star4"] for r in rs if r["visit"])
            out.append({"group": " × ".join(map(str, k)) if isinstance(k, tuple) else k, "slots": n, "participants": len({r["participant_id"] for r in rs}),
                        "visits": v, "visit_rate": round(v / n, 4) if n else None,
                        "visit30": sum(r["visit30"] for r in rs), "visit30_rate": round(sum(r["visit30"] for r in rs) / n, 4) if n else None,
                        "rated_given_visit": round(rated / v, 4) if v else None, "star4_given_rated": round(s4 / rated, 4) if rated else None,
                        "visit_star4": sum(r["visit_star4"] for r in rs), "visit_star4_rate": round(sum(r["visit_star4"] for r in rs) / n, 4) if n else None,
                        "serendipity": sum(r["serendipity"] for r in rs),
                        "median_minutes_to_visit": sorted(r["minutes_to_visit"] for r in rs if r["visit"])[v // 2] if v else None})
        return out
    bands = [(0, 59), (60, 99), (100, 199), (200, 299), (300, 399), (400, 10 ** 9)]
    band = lambda n: next((f"{a}-{b if b < 10 ** 9 else ''}" for a, b in bands if n is not None and a <= n <= b), "不明")
    summaries = [
        ("戦略別ファネル（当日・PRESURVEY除く）", funnel(day_rec, lambda r: r["strategy"])),
        ("arm別ファネル（DRSA系 = DRSA+SER）", funnel(day_rec, lambda r: r["arm"])),
        ("arm × interest_match（図③）", funnel(day_rec, lambda r: (r["arm"], r["interest_match"]))),
        ("期間 × 戦略（ABA）", funnel(day_rec, lambda r: (r["period"], r["strategy"]))),
        ("期間 × 戦略・観察30分以上（visit30 を見る）", funnel([r for r in day_rec if r["exposure_min"] >= 30], lambda r: (r["period"], r["strategy"]))),
        ("枠種別 slot × 戦略", funnel(day_rec, lambda r: (r["slot"], r["strategy"]))),
        ("何回目の解放 × arm", funnel(day_rec, lambda r: (r["unlock_no"], r["arm"]))),
        ("決定表件数帯（図④・記述のみ）", funnel(day_rec, lambda r: band(r["decision_table_size"]))),
        ("主要分析の外の枠（参考: PRESURVEY・FALLBACK。visited は解放時刻以降の訪問）", funnel([r for r in rec_rows if r["strategy"] in ("PRESURVEY", "FALLBACK_COVERAGE")], lambda r: r["strategy"])),
    ]

    # ---- 参加者内比較（図②の素材） ----
    within = []
    for p, rs in recs_by_pid.items():
        rs = [r for r in rs if r["is_event_day"] and r["arm"] in ("RANDOM", "DRSA系")]
        a_ = [r for r in rs if r["arm"] == "DRSA系"]; b_ = [r for r in rs if r["arm"] == "RANDOM"]
        if a_ and b_:
            ra, rb = sum(r["visit"] for r in a_) / len(a_), sum(r["visit"] for r in b_) / len(b_)
            within.append({"participant_id": p, "slots_DRSA系": len(a_), "visits_DRSA系": sum(r["visit"] for r in a_), "rate_DRSA系": round(ra, 4),
                           "slots_RANDOM": len(b_), "visits_RANDOM": sum(r["visit"] for r in b_), "rate_RANDOM": round(rb, 4), "diff": round(ra - rb, 4),
                           "visit_star4_rate_DRSA系": round(sum(r["visit_star4"] for r in a_) / len(a_), 4),
                           "visit_star4_rate_RANDOM": round(sum(r["visit_star4"] for r in b_) / len(b_), 4)})
    within.sort(key=lambda r: r["participant_id"])

    # ---- 規則の発火（図⑤の素材） ----
    rule = defaultdict(lambda: {"fired": 0, "visits": 0, "star4": 0, "sup": [], "conf": [], "first": None, "last": None, "class": None})
    for r, row in zip(slots, rec_rows):
        for x in r["_rp"].get("rules") or []:
            g = rule[x.get("id")]; g["fired"] += 1; g["visits"] += row["visit"]; g["star4"] += row["visit_star4"]; g["class"] = x.get("class")
            g["sup"].append(x.get("support")); g["conf"].append(x.get("confidence"))
            tm = row["unlock_at_jst"]; g["first"] = min(g["first"] or tm, tm); g["last"] = max(g["last"] or tm, tm)
    rule_rows = [{"rule_id": k, "class": g["class"], "fired_on_assigned_slots": g["fired"], "visits": g["visits"],
                  "visit_rate": round(g["visits"] / g["fired"], 4), "visit_star4": g["star4"],
                  "support_min": min(s for s in g["sup"] if s is not None) if any(s is not None for s in g["sup"]) else None,
                  "support_max": max(s for s in g["sup"] if s is not None) if any(s is not None for s in g["sup"]) else None,
                  "confidence_min": min(c for c in g["conf"] if c is not None) if any(c is not None for c in g["conf"]) else None,
                  "confidence_max": max(c for c in g["conf"] if c is not None) if any(c is not None for c in g["conf"]) else None,
                  "first_jst": g["first"], "last_jst": g["last"]} for k, g in sorted(rule.items(), key=lambda x: -x[1]["fired"])]

    # ---- 周遊（Q1・図①の素材） ----
    dist = Counter(r["unique_booths"] for r in p_rows)
    n_all = len(p_rows); n_vis = sum(1 for r in p_rows if r["unique_booths"] > 0); cum_all = cum_vis = 0
    q1 = []
    for k in range(0, max(dist) + 1):
        cum_all += dist.get(k, 0); cum_vis += dist.get(k, 0) if k > 0 else 0
        q1.append({"unique_booths": k, "participants": dist.get(k, 0), "ecdf_all": round(cum_all / n_all, 4),
                   "ecdf_visitors_only": round(cum_vis / n_vis, 4) if k > 0 else None})

    return {"rec": rec_rows, "cand": cand_rows, "unlock": unlock_rows, "ci": ci_rows, "p": p_rows, "b": b_rows,
            "summaries": summaries, "within": within, "rules": rule_rows, "q1": q1,
            "meta": {"drsa_start": drsa_start, "drsa_end": drsa_end, "end": end_t, "n_participants": len(users)}}


COLUMN_DOC = {
    "slot_id": "推薦枠の仮ID（参加者×ブース×解放 = 分析単位）", "participant_id": "参加者の仮名ID（最初の行動順。対応表なし）",
    "unlock_id": "解放イベントの仮ID", "unlock_no": "そのカードで何回目の解放か（PRESURVEY=0）", "unlock_at_jst": "解放時刻（JST）",
    "is_event_day": "1 = 10/16（JST）以降。0 = 前日リハーサル等", "unlock_phase": "解放時のエンジンのフェーズ（COVERAGE/DRSA/PRESURVEY）",
    "unlock_strategy": "解放の種類（RECOMMEND/PRESURVEY/FALLBACK_COVERAGE）", "decision_table_size": "解放時点の決定表の件数",
    "global_checkin_count": "解放時点の全体チェックイン数", "strategy": "その枠を選んだ戦略（reason_payload.strategy。無ければ PRESURVEY）",
    "arm": "比較群。DRSA系 = DRSA+SER（どちらもラフ集合由来）", "period": "ABA 期間。DRSA が最初/最後に出た解放時刻で区切る",
    "slot": "枠種別（REST / MISMATCH_QUOTA）", "scored_by": "スコアを付けたエンジン", "booth_code": "ブース番号（B+表示番号）",
    "interest_match": "推薦時に凍結された関心一致（再計算しない）", "declared": "attributes.raw.declared（宣言関心の一致度）",
    "behavioral": "attributes.raw.behavioral", "score": "推薦スコア", "rank_in_event": "その解放内での順位",
    "visitor_count_at_rec": "推薦時のブース訪問者数", "matched_rules": "一致した規則数", "rule_ids": "根拠になった規則 ID",
    "ser_rule": "SER の規則（JSON）", "card_position": "このブースがカード上にあるマス位置", "card_zone": "CENTER / OUTER",
    "card_source": "マスの出どころ（RECOMMEND/FREE_VISIT/PRESURVEY）", "visited_before_unlock": "解放より前に既に訪問済み",
    "prior_checkins": "解放前のその参加者のチェックイン数（積極性の共変量）", "visit": "【主要指標】解放時刻以降に同ブースへチェックイン",
    "visit_at_jst": "最初の該当チェックイン時刻", "minutes_to_visit": "解放→訪問（分）", "visit30": "30 分以内に訪問",
    "exposure_min": "解放から最後のチェックインまでの観察時間（分）", "rated": "その訪問に評価あり", "rating": "★（4段階）",
    "star4": "【高評価】★4", "visit_star4": "訪問かつ★4（評価欠測は0）", "has_comment": "コメントあり（本文は載せない）",
    "serendipity": "【セレンディピティ】MISMATCH かつ訪問かつ★4", "was_assigned": "1 = 実際に枠として提示された候補",
    "visited_later": "解放以降にその候補ブースを訪問した", "visit_seq": "その参加者の何回目のチェックインか（時刻順）",
    "via_card_cell": "ビンゴのマス経由（cell_id あり）。0 = カード外訪問", "was_recommended_before": "このチェックイン以前に同ブースが推薦枠として出ていた",
    "interest_category_match_posthoc": "アンケートの関心カテゴリにブースのカテゴリが含まれる（事後計算・参考）",
    "seconds_to_rate": "チェックイン→評価（秒）", "rating_prompt": "評価のきっかけ（IMMEDIATE/MANUAL）", "repeat_visit": "同じブースへの再訪",
    "offcard_checkins": "カード外のチェックイン数", "arm_mix": "当日に受けた群の組み合わせ", "stay_min": "最初〜最後のチェックイン（分）",
}



# ============================================================
# 見た目（データは build() のまま。表示だけ整える）
# ============================================================
LABEL = {
    "slot_id": "推薦枠ID", "participant_id": "参加者ID", "unlock_id": "解放ID", "unlock_no": "何回目の解放",
    "unlock_at_jst": "解放時刻", "is_event_day": "当日", "unlock_phase": "解放時フェーズ", "unlock_strategy": "解放の種類",
    "decision_table_size": "決定表件数", "global_checkin_count": "全体チェックイン数", "strategy": "推薦の戦略", "arm": "比較群",
    "period": "期間", "slot": "枠種別", "scored_by": "採点エンジン", "booth_code": "ブース番号", "booth_name": "ブース名",
    "booth_category": "ブース分野", "interest_match": "関心一致", "declared": "宣言関心", "behavioral": "行動関心", "score": "スコア",
    "rank_in_event": "解放内の順位", "visitor_count_at_rec": "推薦時の訪問者数", "matched_rules": "一致規則数", "rule_ids": "規則ID",
    "ser_rule": "SER規則", "card_position": "マス位置", "card_zone": "マス領域", "card_source": "マスの出どころ",
    "visited_before_unlock": "解放前に訪問済", "prior_checkins": "解放前の訪問数", "visit": "訪問", "visit_at_jst": "訪問時刻",
    "minutes_to_visit": "訪問まで(分)", "visit30": "30分以内訪問", "exposure_min": "観察時間(分)", "rated": "評価あり", "rating": "★",
    "star4": "★4", "visit_star4": "訪問かつ★4", "has_comment": "コメントあり", "serendipity": "セレンディピティ",
    "candidate_id": "候補ID", "was_assigned": "提示された", "visited_later": "後で訪問", "phase": "フェーズ", "line_index": "ライン番号",
    "released_positions": "開いたマス", "slots": "枠数", "slots_visited": "訪問された枠", "strategies": "含まれる戦略",
    "visit_seq": "何回目の訪問", "visit_order_db": "visit_order(DB)", "checked_in_at_jst": "チェックイン時刻", "checkin_method": "方法",
    "via_card_cell": "カード経由", "was_recommended_before": "事前に推薦あり", "rec_strategy": "その推薦の戦略",
    "rec_interest_match": "その推薦の関心一致", "interest_category_match_posthoc": "関心分野と一致(事後)", "rating_prompt": "評価のきっかけ",
    "seconds_to_rate": "評価まで(秒)", "repeat_visit": "再訪", "survey_answered": "アンケート回答", "gender": "性別", "age_range": "年代",
    "occupation": "職業", "affiliation": "所属", "exploration_disposition": "探索傾向", "interest_categories": "関心分野",
    "top_interest_category": "一番の関心", "first_checkin_jst": "最初の訪問", "last_checkin_jst": "最後の訪問", "stay_min": "滞在(分)",
    "checkins": "訪問回数", "unique_booths": "訪問ブース数", "offcard_checkins": "カード外訪問", "ratings": "評価数",
    "unlocks": "解放回数", "rec_slots": "推薦枠数", "rec_visits": "推薦先訪問", "arm_mix": "受けた群", "bingo_achieved_cells": "ビンゴ達成マス",
    "gacha_coins_used": "ガチャ枚数", "award_votes": "アワード投票", "is_active": "公開中", "unique_visitors": "訪問人数",
    "mean_rating": "平均★", "star4_rate": "★4率", "candidate_count": "候補になった回数",
    "group": "区分", "participants": "参加者数", "visits": "訪問", "visit_rate": "訪問率", "visit30_rate": "30分以内訪問率",
    "rated_given_visit": "評価率(訪問中)", "star4_given_rated": "★4率(評価中)", "visit_star4_rate": "訪問かつ★4率",
    "median_minutes_to_visit": "訪問まで中央値(分)", "rule_id": "規則ID", "class": "結論", "fired_on_assigned_slots": "発火回数",
    "support_min": "support最小", "support_max": "support最大", "confidence_min": "確信度最小", "confidence_max": "確信度最大",
    "first_jst": "最初", "last_jst": "最後", "ecdf_all": "累積割合(全員)", "ecdf_visitors_only": "累積割合(訪問者のみ)",
    "slots_DRSA系": "DRSA系 枠", "visits_DRSA系": "DRSA系 訪問", "rate_DRSA系": "DRSA系 訪問率", "slots_RANDOM": "RANDOM 枠",
    "visits_RANDOM": "RANDOM 訪問", "rate_RANDOM": "RANDOM 訪問率", "diff": "差(DRSA系−RANDOM)",
    "visit_star4_rate_DRSA系": "DRSA系 訪問かつ★4率", "visit_star4_rate_RANDOM": "RANDOM 訪問かつ★4率", "column": "列", "meaning": "意味",
}
for _s in ("RANDOM", "DRSA", "SER", "EXPLORE"):
    LABEL[f"slots_{_s}"] = f"{_s} 枠"; LABEL[f"visits_{_s}"] = f"{_s} 訪問"
FLAGS = {"is_event_day", "visit", "visit30", "rated", "star4", "visit_star4", "has_comment", "serendipity", "was_assigned", "visited_later",
         "via_card_cell", "was_recommended_before", "visited_before_unlock", "repeat_visit", "survey_answered", "interest_category_match_posthoc"}
GROUP_JA = {"RANDOM": "RANDOM（ランダム）", "DRSA": "DRSA（規則）", "SER": "SER（意外な出会い）", "EXPLORE": "EXPLORE（探索）",
            "DRSA系": "DRSA系（DRSA+SER）", "PRESURVEY": "PRESURVEY（事前アンケート）", "FALLBACK_COVERAGE": "FALLBACK（フォールバック）",
            "MATCH": "関心一致", "PARTIAL": "一部一致", "MISMATCH": "関心外", "UNKNOWN": "不明",
            "P1_RANDOM_early": "① 前半ランダム", "P2_DRSA": "② DRSA期", "P3_RANDOM_late": "③ 後半ランダム",
            "REST": "通常枠", "MISMATCH_QUOTA": "関心外枠"}
HEAD_FILL = PatternFill("solid", fgColor="1F2937"); HEAD_FONT = Font(bold=True, color="FFFFFF")
SUB_FONT = Font(size=8, color="6B7280"); TITLE_FONT = Font(bold=True, size=13); NOTE_FONT = Font(size=10, color="374151")
BAND = PatternFill("solid", fgColor="F3F4F6"); THIN = Side(style="thin", color="E5E7EB")


def fmt_for(col):
    if col in FLAGS:
        return '"○";;""'                       # 1 → ○、0 → 空欄（値は 1/0 のまま）
    if col.endswith("_jst"):
        return "m/d hh:mm"
    if "rate" in col or col.startswith("ecdf") or col in ("diff", "star4_rate"):
        return "0.0%"
    if col in ("score", "mean_rating") or col.startswith(("support", "confidence")):
        return "0.000"
    if col in ("minutes_to_visit", "exposure_min", "stay_min", "median_minutes_to_visit"):
        return "0.0"
    return None


def ja_group(v):
    if isinstance(v, str) and " × " in v:
        return " × ".join(GROUP_JA.get(x, x) for x in v.split(" × "))
    return GROUP_JA.get(v, v)


def write(wb, title, rows, cols=None, note=None, heading=None, bars=(), start=1, ws=None):
    """1行目: 見出し（日本語）/ 2行目: 変数名（小さく灰色）/ 3行目〜: データ。
    pandas では header=1（変数名の行）で読める。"""
    ws = ws or wb.create_sheet(title[:31])
    r0 = start
    if heading:
        ws.cell(r0, 1, heading).font = TITLE_FONT; r0 += 1
    if note:
        c = ws.cell(r0, 1, note); c.font = NOTE_FONT; r0 += 1
    if heading or note:
        r0 += 0
    if not rows:
        ws.cell(r0, 1, "（該当なし）"); return ws, r0 + 1
    cols = cols or list(rows[0].keys())
    for j, c in enumerate(cols, 1):
        h = ws.cell(r0, j, LABEL.get(c, c)); h.fill, h.font = HEAD_FILL, HEAD_FONT
        h.alignment = Alignment(wrap_text=True, vertical="center")
        v = ws.cell(r0 + 1, j, c); v.font = SUB_FONT
    widths = [max(6, len(LABEL.get(c, c)) * 2 + 2) for c in cols]
    for i, r in enumerate(rows):
        rr = r0 + 2 + i
        for j, c in enumerate(cols, 1):
            v = r.get(c)
            if c == "group":
                v = ja_group(v)
            x = ws.cell(rr, j, v)
            if isinstance(v, str):
                x.data_type = "s"
            f = fmt_for(c)
            if f:
                x.number_format = f
            if c in FLAGS:
                x.alignment = Alignment(horizontal="center")
            if i < 200 and v is not None:
                widths[j - 1] = min(48, max(widths[j - 1], len(str(v)) * (2 if isinstance(v, str) and not v.isascii() else 1) + 2))
        if i % 2:
            for j in range(1, len(cols) + 1):
                ws.cell(rr, j).fill = BAND
    if start == 1:
        for j, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(j)].width = w
        ws.freeze_panes = ws.cell(r0 + 2, 2)
        ws.auto_filter.ref = f"A{r0 + 1}:{get_column_letter(len(cols))}{r0 + 1 + len(rows)}"
    last = r0 + 1 + len(rows)
    for c in bars:
        if c in cols:
            L = get_column_letter(cols.index(c) + 1)
            ws.conditional_formatting.add(f"{L}{r0 + 2}:{L}{last}", DataBarRule(start_type="num", start_value=0, end_type="num", end_value=1, color="60A5FA"))
    return ws, last + 1


FUNNEL_COLS = ["group", "slots", "participants", "visits", "visit_rate", "visit30_rate", "rated_given_visit", "star4_given_rated",
               "visit_star4", "visit_star4_rate", "serendipity", "median_minutes_to_visit"]
SUMMARY_NOTE = {
    0: "読み方: 訪問率 = 推薦された枠のうち、解放後にそのブースへ行った割合（主要指標）。群は無作為化していないので差は記述として読む。",
    1: "DRSA系 = DRSA + SER（どちらもラフ集合由来）。ANALYSIS.md の主要比較と同じ区分。",
    2: "図③。関心外（MISMATCH）の行がセレンディピティの主役。関心一致と比べて落ちていないかを見る。",
    3: "ABA。② DRSA期 の前後がランダム。時刻の交絡を部分的に確かめられる。",
    4: "観察時間が30分以上ある枠に限定し、30分以内訪問率で比べる（観察時間の違いを揃えた感度分析）。",
    5: "通常枠 / 関心外枠（MISMATCH_QUOTA）別。",
    6: "解放の回数が増えると訪問率が変わるか。",
    7: "図④。決定表件数（データ量）帯別。フェーズと時刻が交絡するので記述のみ。",
    8: "主要分析に入れない枠（参考）。",
}
SUMMARY_TITLE = ["戦略別", "比較群別", "群×関心一致", "期間×戦略(ABA)", "期間×戦略(観察30分以上)", "枠種別×戦略", "解放回数×群",
                 "決定表件数帯", "主要分析外の枠"]


def main(dump: Path):
    d = D(dump)
    R = build(d)
    M = R["meta"]
    wb = Workbook(); wb.remove(wb.active)

    # ---- 0. 要点 ----
    ws = wb.create_sheet("要点")
    ws.column_dimensions["A"].width = 30
    for L in "BCDEFGHIJKL":
        ws.column_dimensions[L].width = 14
    ev = d.t["events"][0]["name"] if d.t["events"] else ""
    ws.cell(1, 1, f"推薦評価 研究データ — {ev}").font = Font(bold=True, size=15)
    ws.cell(2, 1, "推薦（DRSA・SER）とランダム推薦で、訪問・評価・セレンディピティがどう違ったかを見るためのデータ。詳しい定義は README と 列定義。").font = NOTE_FONT
    day = [r for r in R["rec"] if r["is_event_day"] and r["strategy"] in ("RANDOM", "DRSA", "SER", "EXPLORE")]
    kv = [("対象の参加者（participant）", M["n_participants"]),
          ("当日チェックインした参加者", len({c["participant_id"] for c in R["ci"] if c["is_event_day"]})),
          ("当日チェックイン", sum(1 for c in R["ci"] if c["is_event_day"])),
          ("主要分析の推薦枠", len(day)), ("推薦を受けた参加者", len({r["participant_id"] for r in day})),
          ("DRSA が動いていた時間", f"{M['drsa_start']:%H:%M} 〜 {M['drsa_end']:%H:%M}（前後はランダム）"),
          ("両方の群を受けた参加者（参加者内比較）", len(R["within"]))]
    for i, (k, v) in enumerate(kv, 4):
        ws.cell(i, 1, k).font = Font(bold=True); ws.cell(i, 2, v)
    row = 4 + len(kv) + 1
    _, row = write(wb, "", R["summaries"][1][1], cols=FUNNEL_COLS, heading="比較群ごとの結果（主要）", note=SUMMARY_NOTE[0],
                   bars=("visit_rate", "visit_star4_rate"), start=row, ws=ws)
    _, row = write(wb, "", R["summaries"][0][1], cols=FUNNEL_COLS, heading="戦略ごとの結果", note="SER = セレンディピティ規則が選んだ関心外ブース / EXPLORE = 関心外枠を探索で選んだもの",
                   bars=("visit_rate", "visit_star4_rate"), start=row + 1, ws=ws)
    _, row = write(wb, "", R["summaries"][3][1], cols=FUNNEL_COLS, heading="時間帯（ABA）ごとの結果", note=SUMMARY_NOTE[3],
                   bars=("visit_rate",), start=row + 1, ws=ws)
    if R["within"]:
        diffs = sorted(x["diff"] for x in R["within"])
        ws.cell(row + 1, 1, "参加者内の差（DRSA系 − RANDOM の訪問率）").font = TITLE_FONT
        ws.cell(row + 2, 1, "同じ人の中での差。0 より右に寄っていれば DRSA系 が良い。分布は「参加者内比較」シート。").font = NOTE_FONT
        for i, (k, v) in enumerate([("人数", len(diffs)), ("平均", sum(diffs) / len(diffs)), ("中央値", diffs[len(diffs) // 2]),
                                    ("DRSA系 が上", sum(1 for x in diffs if x > 0)), ("同じ", sum(1 for x in diffs if x == 0)),
                                    ("RANDOM が上", sum(1 for x in diffs if x < 0))], row + 3):
            ws.cell(i, 1, k); c = ws.cell(i, 2, v)
            if k in ("平均", "中央値"):
                c.number_format = "+0.0%;-0.0%;0.0%"
        row += 10
    ws.cell(row + 1, 1, "注意").font = TITLE_FONT
    for i, t in enumerate(["群は無作為化していない。来場時刻・ゲート状態と交絡しているので、差を因果として読まない。",
                           "★4 が約8割（天井効果）。★4 の比較は副次扱い。",
                           "推薦すること自体の効果（Q4）は対照群がないので主張しない。",
                           "検定・効果量は db-archive/2026-10-16-prod-event/ANALYSIS.md を参照。"], row + 2):
        ws.cell(i, 1, "・" + t).font = NOTE_FONT

    # ---- README ----
    rd = wb.create_sheet("README")
    rd.column_dimensions["A"].width = 26; rd.column_dimensions["B"].width = 110
    lines = [
        ("対象", f"role = participant の {M['n_participants']} 人（チェックイン0件・カード未生成も残す）。出展者・運営・閲覧者は除外"),
        ("個人情報", "名前・メールは載せない。参加者ID（P0001…）は仮名で対応表なし。属性の組み合わせで3人未満になる集計は外に出さない"),
        ("時刻", "すべて日本時間。UTC 保存の check_ins.checked_in_at / bingo_cells.* / gacha_coin_uses.used_at を +9h"),
        ("主要指標（事前登録）", "訪問 = 解放時刻以降に、同じ参加者が同じブースへチェックイン"),
        ("高評価（事前登録）", "★4（2026-10-10 変更）"),
        ("セレンディピティ（事前登録）", "関心外（MISMATCH）かつ訪問かつ★4"),
        ("主要分析の母集団", "推薦枠のうち当日かつ戦略が RANDOM / DRSA / SER / EXPLORE（PRESURVEY とフォールバック6枠は除く。ANALYSIS.md と同じ）"),
        ("比較の注意", "参加者内ランダム化は実施していない（ADR 0010）。群は来場時刻・ゲート状態と交絡"),
        ("DRSA の期間", f"{M['drsa_start']:%m/%d %H:%M} 〜 {M['drsa_end']:%m/%d %H:%M}。前後は RANDOM（ABA 型）"),
        ("観察の終わり", f"最後のチェックイン {M['end']:%m/%d %H:%M}（観察時間の基準）"),
        ("表の見方", "1行目 = 日本語の見出し、2行目 = 変数名（灰色）。○ = 1、空欄 = 0（セルの値は 1/0 のまま）。pandas では header=1 で読める"),
        ("加工の範囲", "結合・分類・数え上げのみ。検定は db-archive/2026-10-16-prod-event/scripts と ANALYSIS.md"),
    ]
    rd.cell(1, 1, "README").font = Font(bold=True, size=15)
    for i, (k, v) in enumerate(lines, 3):
        rd.cell(i, 1, k).font = Font(bold=True); c = rd.cell(i, 2, v); c.alignment = Alignment(wrap_text=True, vertical="top")
    sheets = [("推薦枠", "参加者×ブース×解放", "主要分析（Q2・Q3）。1行 = 1枠"), ("候補スコア", "候補全件", "提示されなかった候補との比較"),
              ("解放イベント", "解放1回", "フェーズ・決定表件数の推移"), ("チェックイン", "訪問1回", "推薦経由かカード外か・評価・再訪"),
              ("参加者", "参加者", "属性・滞在・受けた群・活動量"), ("ブース", "ブース", "人気・推薦された回数"),
              ("集計_*", "区分", "ファネル（記述のみ）"), ("参加者内比較", "両群を受けた人", "図②"), ("規則の発火", "規則", "図⑤"),
              ("周遊_Q1", "訪問ブース数", "図①（去年金曜192名の中央値 6.0）"), ("列定義", "列", "全シートの列の意味")]
    r0 = len(lines) + 4
    for j, h in enumerate(["シート", "単位", "使い道"], 1):
        c = rd.cell(r0, j, h); c.fill, c.font = HEAD_FILL, HEAD_FONT
    for i, s in enumerate(sheets, r0 + 1):
        for j, v in enumerate(s, 1):
            rd.cell(i, j, v)

    counts = {}
    write(wb, "推薦枠", R["rec"], bars=()); counts["推薦枠"] = len(R["rec"])
    write(wb, "候補スコア", R["cand"]); counts["候補スコア"] = len(R["cand"])
    write(wb, "解放イベント", R["unlock"]); counts["解放イベント"] = len(R["unlock"])
    write(wb, "チェックイン", R["ci"]); counts["チェックイン"] = len(R["ci"])
    write(wb, "参加者", R["p"]); counts["参加者"] = len(R["p"])
    write(wb, "ブース", R["b"], bars=("star4_rate",)); counts["ブース"] = len(R["b"])
    for i, (name, rows) in enumerate(R["summaries"]):
        title = f"集計_{SUMMARY_TITLE[i]}"
        write(wb, title, rows, cols=FUNNEL_COLS, heading=name, note=SUMMARY_NOTE.get(i), bars=("visit_rate", "visit_star4_rate"))
        counts[title] = len(rows)
    write(wb, "参加者内比較", R["within"], heading="参加者内比較（図②）",
          note="当日、RANDOM と DRSA系 の両方を受けた参加者。差 = DRSA系 訪問率 − RANDOM 訪問率。0 より右に寄れば DRSA系 が良い")
    counts["参加者内比較"] = len(R["within"])
    write(wb, "規則の発火", R["rules"], heading="規則の発火（図⑤）", note="推薦枠（提示されたもの）の根拠になった規則を数えたもの", bars=("visit_rate",))
    counts["規則の発火"] = len(R["rules"])
    write(wb, "周遊_Q1", R["q1"], heading="周遊（Q1・図①）", note="当日のユニーク訪問ブース数の分布。累積割合(全員) はチェックイン0件を含む", bars=("ecdf_all",))
    counts["周遊_Q1"] = len(R["q1"])
    cols = sorted({c for k in ("rec", "cand", "unlock", "ci", "p", "b") for r in R[k][:1] for c in r})
    write(wb, "列定義", [{"column": c, "group": LABEL.get(c, ""), "meaning": COLUMN_DOC.get(c, "")} for c in cols], cols=["group", "column", "meaning"])
    wb["列定義"].column_dimensions["C"].width = 90

    out = dump / "export"
    out.mkdir(exist_ok=True); os.chmod(out, 0o700)
    path = out / "推薦評価_研究データ_20261019.xlsx"
    wb.save(path); os.chmod(path, 0o600)
    for k, v in counts.items():
        print(f"  {k}: {v} 行")
    print(f"出力: {path}")
    return R


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
