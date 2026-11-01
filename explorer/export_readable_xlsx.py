"""完全ダンプから、非エンジニア向けの「イベントまとめ」xlsx を作る。

使い方（event-support-analytics で）:
    .venv/bin/python explorer/export_readable_xlsx.py ../db-archive/2026-10-16-prod-event/full

- ID は出さず、表示名・ブース名・アワード名・日本語の選択肢に置き換える
- 時刻はすべて日本時間（JST）に揃える。DB は列によって JST/UTC が混在しているので、UTC の列だけ +9 時間する
  （UTC: check_ins.checked_in_at / bingo_cells.* / gacha_coin_uses.used_at / events.date_*。2026-10-19 にダンプで実測確認）
- 「誰が・いつ・どこで・何をしたか」の流れが追える並べ方にする
- **値は標準出力に出さない。** 出すのはシート名と行数だけ。出力は 600
"""

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

UTC_COLUMNS = {("check_ins", "checked_in_at"), ("bingo_cells", "assigned_at"), ("bingo_cells", "achieved_at"),
               ("gacha_coin_uses", "used_at"), ("events", "date_start"), ("events", "date_end")}
JA = {
    "teens": "10代", "twenties": "20代", "thirties": "30代", "forties": "40代", "fifties_plus": "50代以上",
    "student": "学生", "planner": "企画職", "teacher": "教員", "designer": "デザイナー", "engineer": "エンジニア",
    "staff": "職員", "other": "その他", "male": "男性", "female": "女性", "prefer_not_to_say": "回答しない",
    "internal": "学内", "external": "学外", "high": "高い", "mid": "ふつう", "low": "低い",
    "participant": "参加者", "exhibitor": "出展者", "manager": "運営", "viewer": "閲覧者",
    "RECOMMEND": "おすすめで開いたマス", "FREE_VISIT": "自由に訪問して開いたマス", "PRESURVEY": "事前アンケートで開いたマス",
}
STRATEGY = {"RANDOM": "ランダムに選んだおすすめ", "DRSA": "好みの傾向から選んだおすすめ",
            "SER": "あえて関心の外から選んだおすすめ（意外な出会い）", "EXPLORE": "まだ行っていない分野からのおすすめ"}
HEAD_FILL = PatternFill("solid", fgColor="1F2937")
HEAD_FONT = Font(bold=True, color="FFFFFF")


def ja(v):
    return JA.get(v, v) if v is not None else ""


class Data:
    def __init__(self, dump: Path):
        m = json.loads((dump / "manifest.json").read_text(encoding="utf-8"))
        sens = m.get("sensitive", {})
        self.t = {}
        for name, info in m["tables"].items():
            src = sens.get(name, info)
            self.t[name] = pq.read_table(dump / src["file"]).to_pylist()
        by = lambda name, key="id": {r[key]: r for r in self.t[name]}
        self.users, self.booths, self.awards, self.cats = by("users"), by("booths"), by("awards"), by("categories")
        self.cards = by("bingo_cards")
        self.card_of = {c["user_id"]: c for c in self.t["bingo_cards"]}
        self.ci = by("check_ins")
        self.rating_of = {r["checkin_id"]: r for r in self.t["booth_ratings"]}
        self.survey = {r["user_id"]: r for r in self.t["user_survey_answers"]}
        self.questions = sorted(self.t["survey_questions"], key=lambda q: int(q["display_order"] or 0))
        self.visits_by_user = defaultdict(list)
        for c in self.t["check_ins"]:
            self.visits_by_user[c["user_id"]].append(c)
        for v in self.visits_by_user.values():
            v.sort(key=lambda c: int(c["visit_order"] or 0))
        self.visited_pairs = {(c["user_id"], c["booth_id"]) for c in self.t["check_ins"]}
        self.recs = [r for r in self.t["recommendation_scores"] if r["was_assigned"] == "1"]
        # 同じ表示名の人は登録順に（2）（3）… を付けて区別する
        self.label = {}
        seen = Counter(u["display_name"] for u in self.t["users"] if u["display_name"])
        nth = Counter()
        for u in sorted(self.t["users"], key=lambda u: (u.get("created_at") or "", u["id"])):
            n = u["display_name"]
            if n and seen[n] > 1:
                nth[n] += 1
                self.label[u["id"]] = n if nth[n] == 1 else f"{n}（{nth[n]}）"
        self.exhibitor_of = defaultdict(list)
        for x in self.t["exhibitor_booths"]:
            self.exhibitor_of[x["booth_id"]].append(x["user_id"])

    # ---- 表示 ----
    def jst(self, table, col, v):
        if not v:
            return None
        d = datetime.fromisoformat(str(v).replace("Z", ""))
        return d + timedelta(hours=9) if (table, col) in UTC_COLUMNS else d

    def user(self, uid):
        u = self.users.get(uid)
        if not u:
            return "（不明なユーザー）"
        return self.label.get(uid) or u["display_name"] or f"（名前未設定・{uid[:4]}）"

    def booth(self, bid):
        b = self.booths.get(bid)
        return f"No.{b['display_code']} {b['name']}" if b else "（不明なブース）"

    def cat(self, cid):
        return self.cats.get(cid, {}).get("name", "（不明）")

    def answers(self, uid):
        s = self.survey.get(uid)
        if not s:
            return {}
        try:
            a = json.loads(s["custom_answers"] or "{}")
        except ValueError:
            a = {}
        for k in ("age_range", "occupation", "industry"):
            if s.get(k) and k not in a:
                a[k] = s[k]
        return a

    def answer_text(self, key, v):
        if v is None:
            return ""
        vs = v if isinstance(v, list) else [v]
        if key in ("interest_categories", "top_interest_category"):
            return "、".join(self.cat(x) for x in vs)
        return "、".join(str(ja(x)) for x in vs)

    @staticmethod
    def strategy(r):
        try:
            s = json.loads(r["reason_payload"] or "{}").get("strategy")
        except ValueError:
            s = None
        return STRATEGY.get(s, s or "不明")


# ---- シートを書く ----
THICK = Side(style="medium", color="111827")


def sheet(wb, title, header, rows, widths=None, wrap=(), group=False):
    """group=True: A列が前の行と同じなら空欄にし、A列が変わる行の上に太線を引く（並び順はそのまま）"""
    ws = wb.create_sheet(title)
    ws.append(header)
    for c in ws[1]:
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for r in rows:
        ws.append(["" if v is None else v for v in r])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if isinstance(c.value, str):
                c.data_type = "s"      # "=" で始まる名前やコメントを数式にしない
            if isinstance(c.value, datetime):
                c.number_format = "m/d hh:mm"
    for i, w in enumerate(widths or [], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for i in wrap:
        for c in ws[get_column_letter(i)][1:]:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    if group and rows:
        prev = object()
        for i, r in enumerate(rows, start=2):
            if r[0] == prev:
                ws.cell(row=i, column=1).value = None
            else:
                if i > 2:
                    for c in ws[i]:
                        c.border = Border(top=THICK)
                ws.cell(row=i, column=1).font = Font(bold=True)
                prev = r[0]
    ws.freeze_panes = "B2"
    if rows and not group:   # まとまり表示のシートは並べ替えると崩れるので、絞り込みボタンを付けない
        ws.auto_filter.ref = ws.dimensions
    return ws


def build(d: Data, path: Path):
    wb = Workbook()
    wb.remove(wb.active)
    P = [u for u in d.t["users"] if u["role"] == "participant"]
    EX = [u for u in d.t["users"] if u["role"] == "exhibitor"]
    attendees = {u["id"] for u in P + EX}
    ev = d.t["events"][0] if d.t["events"] else {}

    # 1. 概要
    visitors = {c["user_id"] for c in d.t["check_ins"]}
    rated = d.t["booth_ratings"]
    comments = [r for r in rated if (r["comment"] or "").strip()]
    rec_hit = [r for r in d.recs if (r["user_id"], r["booth_id"]) in d.visited_pairs]
    votes = d.t["award_votes"]
    gacha = d.t["gacha_coin_uses"]
    cells = d.t["bingo_cells"]
    start = d.jst("events", "date_start", ev.get("date_start"))
    summary = [
        ("イベント名", ev.get("name", "")),
        ("開催日", start.strftime("%Y年%m月%d日") if start else ""),
        ("会場", ev.get("venue") or ""),
        ("", ""),
        ("合計参加者数（参加者＋出展者）", f"{len(attendees)} 人（参加者 {len(P)} 人・出展者 {len(EX)} 人）"),
        ("ブースに1回以上チェックインした人（参加者＋出展者）", f"{len(attendees & visitors)} 人"),
        ("", "以下は全員（運営なども含む）の合計です。役割ごとの内訳は「2.役割ごとの集計」を見てください。"),
        ("チェックイン（ブース訪問）の合計", f"{len(d.t['check_ins'])} 回"),
        ("1人あたりの訪問ブース数（訪問した人の平均）", f"{len(d.t['check_ins']) / max(len(visitors), 1):.1f} ブース"),
        ("ブースへの評価", f"{len(rated)} 件（うちコメント付き {len(comments)} 件）"),
        ("おすすめとして表示されたブース", f"{len(d.recs)} 件（{len({r['user_id'] for r in d.recs})} 人に）"),
        ("おすすめされたブースに実際に行った", f"{len(rec_hit)} 件（{len(rec_hit) / max(len(d.recs), 1) * 100:.1f}%）"),
        ("アワード投票", f"{len(votes)} 票（{len({v['user_id'] for v in votes})} 人が投票）"),
        ("ガチャコインの使用", f"{len(gacha)} 枚（{len({g['user_id'] for g in gacha})} 人が使用）"),
        ("ビンゴで開いたマス", f"{sum(c['is_achieved'] == '1' for c in cells)} マス（カード {len(d.t['bingo_cards'])} 枚）"),
        ("事前アンケートに答えた人", f"{len(d.t['user_survey_answers'])} 人"),
        ("ブース数", f"{len(d.t['booths'])} ブース（出展者 {len({x['user_id'] for x in d.t['exhibitor_booths']})} 人）"),
        ("", ""),
        ("このファイルの見方", "下のシートを左から順に見ると、全体 → 役割 → 人 → ブース → 投票・ガチャ・ビンゴ の順に詳しくなります。"
                         "どのシートも見出しの ▼ で並べ替え・絞り込みができます。"),
    ]
    ws = sheet(wb, "1.概要", ["項目", "内容"], summary, widths=[46, 90], wrap=(2,))

    # 2. 役割ごとの集計
    ROLE_ORDER = ["participant", "exhibitor", "manager", "viewer"]
    by_role = defaultdict(list)
    for u in d.t["users"]:
        by_role[u["role"]].append(u["id"])
    def role_row(label, ids):
        ids = set(ids)
        ci = [c for c in d.t["check_ins"] if c["user_id"] in ids]
        rt = [r for r in rated if r["user_id"] in ids]
        vt = [v for v in votes if v["user_id"] in ids]
        gc = [g for g in gacha if g["user_id"] in ids]
        return [label, len(ids), len({c["user_id"] for c in ci}), len(ci), len(rt), sum(1 for r in rt if (r["comment"] or "").strip()),
                len(vt), len({v["user_id"] for v in vt}), len(gc), len({g["user_id"] for g in gc}),
                sum(1 for u in ids if u in d.survey)]
    roles = sorted(by_role, key=lambda r: ROLE_ORDER.index(r) if r in ROLE_ORDER else 99)
    rows = [role_row(ja(r), by_role[r]) for r in roles]
    rows.append(role_row("合計参加者（参加者＋出展者）", attendees))
    rows.append(role_row("全員", [u["id"] for u in d.t["users"]]))
    ws = sheet(wb, "2.役割ごとの集計", ["役割", "人数", "訪問した人数", "訪問回数", "評価", "コメント", "アワード投票", "投票した人数",
                                   "ガチャ使用枚数", "ガチャを使った人数", "アンケート回答"], rows, widths=[30] + [12] * 10)
    for r in (ws.max_row - 1, ws.max_row):
        for c in ws[r]:
            c.font = Font(bold=True)
    for c in ws[ws.max_row - 1]:
        c.border = Border(top=THICK)

    # 2. 参加者一覧
    rows = []
    for u in sorted(d.t["users"], key=lambda u: (u["role"] != "participant", -len(d.visits_by_user[u["id"]]))):
        uid = u["id"]; a = d.answers(uid); vs = d.visits_by_user[uid]
        my_r = [d.rating_of.get(c["id"]) for c in vs]; my_r = [r for r in my_r if r]
        my_rec = [r for r in d.recs if r["user_id"] == uid]
        card = d.card_of.get(uid)
        times = [d.jst("check_ins", "checked_in_at", c["checked_in_at"]) for c in vs]
        rows.append([
            d.user(uid), u.get("email") or "", ja(u["role"]),
            d.answer_text("age_range", a.get("age_range")), d.answer_text("occupation", a.get("occupation")),
            d.answer_text("affiliation", a.get("affiliation")), d.answer_text("gender", a.get("gender")),
            d.answer_text("interest_categories", a.get("interest_categories")),
            len(vs), min(times) if times else None, max(times) if times else None,
            len(my_r), sum(1 for r in my_r if (r["comment"] or "").strip()),
            len(my_rec), sum(1 for r in my_rec if (uid, r["booth_id"]) in d.visited_pairs),
            sum(1 for v in votes if v["user_id"] == uid), sum(1 for g in gacha if g["user_id"] == uid),
            sum(1 for c in cells if card and c["card_id"] == card["id"] and c["is_achieved"] == "1"),
        ])
    sheet(wb, "3.参加者一覧", ["名前", "メールアドレス", "立場", "年代", "職業", "所属", "性別", "関心のある分野",
                             "訪問したブース数", "最初の訪問", "最後の訪問", "評価した数", "コメントした数",
                             "おすすめされた数", "おすすめに行った数", "アワード投票数", "ガチャを使った数", "ビンゴで開いたマス"], rows,
          widths=[20, 28, 8, 8, 12, 8, 8, 30, 10, 12, 12, 9, 9, 10, 10, 10, 10, 10])

    # 3. 行動の流れ（参加者ごと・時刻順）
    flow = []
    for r in d.t["user_survey_answers"]:
        flow.append((r["user_id"], d.jst("user_survey_answers", "created_at", r["created_at"]), "事前アンケートに回答", "", ""))
    for r in d.recs:
        flow.append((r["user_id"], d.jst("recommendation_scores", "created_at", r["created_at"]), "おすすめが表示された",
                     d.booth(r["booth_id"]), d.strategy(r)))
    for c in d.t["check_ins"]:
        rec = any(r["user_id"] == c["user_id"] and r["booth_id"] == c["booth_id"] for r in d.recs)
        flow.append((c["user_id"], d.jst("check_ins", "checked_in_at", c["checked_in_at"]), "ブースを訪問",
                     d.booth(c["booth_id"]), f"{c['visit_order']} か所目" + ("（おすすめされていたブース）" if rec else "")))
    for r in rated:
        flow.append((r["user_id"], d.jst("booth_ratings", "rated_at", r["rated_at"]), "ブースを評価",
                     d.booth(r["booth_id"]), f"★{r['rating']}（{r['scale']}段階）" + (f"　コメント:「{r['comment']}」" if (r["comment"] or "").strip() else "")))
    for g in gacha:
        flow.append((g["user_id"], d.jst("gacha_coin_uses", "used_at", g["used_at"]), "ガチャを回した", "", f"{int(g['coin_index']) + 1} 枚目のコイン"))
    for v in votes:
        flow.append((v["user_id"], d.jst("award_votes", "created_at", v["created_at"]), "アワードに投票",
                     d.booth(v["booth_id"]), d.awards.get(v["award_id"], {}).get("name", "")))
    first = {}
    for uid, t, *_ in flow:
        if t and (uid not in first or t < first[uid]):
            first[uid] = t
    flow.sort(key=lambda x: (first.get(x[0]) or datetime.max, x[0], x[1] or datetime.max))
    sheet(wb, "4.参加者ごとの行動の流れ", ["名前", "時刻", "したこと", "ブース", "くわしく"],
          [(d.user(u), t, a, b, x) for u, t, a, b, x in flow], widths=[20, 12, 18, 45, 70], wrap=(5,), group=True)

    # 4. ブース一覧
    rows = []
    for b in sorted(d.t["booths"], key=lambda b: int(b["display_code"] or 0)):
        bid = b["id"]
        bc = [c for c in d.t["check_ins"] if c["booth_id"] == bid]
        br = [r for r in rated if r["booth_id"] == bid]
        stars = Counter(r["rating"] for r in br)
        brec = [r for r in d.recs if r["booth_id"] == bid]
        bv = Counter(d.awards.get(v["award_id"], {}).get("name", "") for v in votes if v["booth_id"] == bid)
        rows.append([
            int(b["display_code"] or 0), b["name"], d.cat(b["category_id"]), len(bc), len({c["user_id"] for c in bc}),
            len(br), round(sum(int(r["rating"]) for r in br) / len(br), 2) if br else None,
            stars.get("4", 0), stars.get("3", 0), stars.get("2", 0), stars.get("1", 0),
            sum(1 for r in br if (r["comment"] or "").strip()),
            sum(bv.values()), "、".join(f"{k} {n}票" for k, n in bv.most_common()),
            len(brec), sum(1 for r in brec if (r["user_id"], bid) in d.visited_pairs),
            "、".join(d.user(u) for u in d.exhibitor_of[bid]),
        ])
    sheet(wb, "5.ブース一覧", ["番号", "ブース名", "分野", "訪問回数", "訪問した人数", "評価の数", "平均★", "★4", "★3", "★2", "★1",
                             "コメント数", "アワード得票", "得票の内訳", "おすすめされた回数", "おすすめから来た回数", "出展者"], rows,
          widths=[6, 45, 22, 9, 10, 8, 7, 5, 5, 5, 5, 9, 9, 50, 12, 12, 30], wrap=(14,))

    # 5. ブースごとの訪問者（ブース → 誰がいつ来て、どう評価したか）
    rows = []
    for c in sorted(d.t["check_ins"], key=lambda c: (int(d.booths.get(c["booth_id"], {}).get("display_code") or 0), c["checked_in_at"] or "")):
        r = d.rating_of.get(c["id"])
        rows.append([d.booth(c["booth_id"]), d.jst("check_ins", "checked_in_at", c["checked_in_at"]), d.user(c["user_id"]),
                     f"{c['visit_order']} か所目", f"★{r['rating']}" if r else "評価なし", (r or {}).get("comment") or "",
                     "はい" if any(x["user_id"] == c["user_id"] and x["booth_id"] == c["booth_id"] for x in d.recs) else ""])
    sheet(wb, "6.ブースごとの訪問者", ["ブース", "訪問した時刻", "名前", "その人にとって何か所目", "評価", "コメント", "おすすめされていた"],
          rows, widths=[45, 12, 20, 12, 9, 60, 12], wrap=(6,), group=True)

    # 6. コメント
    rows = [[d.booth(r["booth_id"]), d.user(r["user_id"]), f"★{r['rating']}", r["comment"],
             "非表示にされた" if r["is_hidden"] == "1" else "", d.jst("booth_ratings", "rated_at", r["rated_at"])]
            for r in sorted(comments, key=lambda r: (int(d.booths.get(r["booth_id"], {}).get("display_code") or 0), r["rated_at"] or ""))]
    sheet(wb, "7.ブースへのコメント", ["ブース", "名前", "評価", "コメント", "表示", "時刻"], rows, widths=[45, 20, 7, 80, 12, 12], wrap=(4,), group=True)

    # 7. アワード（結果 → 誰がどこに投票したか）
    order = {a["id"]: int(a["sort_order"] or 0) for a in d.t["awards"]}
    tally = Counter((v["award_id"], v["booth_id"]) for v in votes)
    rows, rank = [], defaultdict(int)
    for (aid, bid), n in sorted(tally.items(), key=lambda x: (order.get(x[0][0], 0), -x[1])):
        rank[aid] += 1
        rows.append([d.awards.get(aid, {}).get("name", ""), rank[aid], d.booth(bid), n])
    sheet(wb, "8.アワード結果", ["アワード", "順位", "ブース", "票数"], rows, widths=[28, 6, 50, 7], group=True)
    rows = [[d.awards.get(v["award_id"], {}).get("name", ""), d.user(v["user_id"]), d.booth(v["booth_id"]),
             d.jst("award_votes", "created_at", v["created_at"])]
            for v in sorted(votes, key=lambda v: (order.get(v["award_id"], 0), v["created_at"] or ""))]
    sheet(wb, "9.アワード投票（誰がどこに）", ["アワード", "投票した人", "投票先のブース", "時刻"], rows, widths=[28, 20, 50, 12], group=True)

    # 10. ガチャ
    used = Counter(g["user_id"] for g in gacha)
    rows = sorted(([d.user(u), n] for u, n in used.items()), key=lambda r: (-r[1], r[0]))
    sheet(wb, "10.ガチャ", ["名前", "使ったコインの枚数"], rows, widths=[24, 18])

    # 11. ビンゴカード（1人1行・マス1〜16）
    rows = []
    for card in d.t["bingo_cards"]:
        cs = {int(c["position"]): c for c in cells if c["card_id"] == card["id"]}
        n = max(cs) if cs else 16
        row = [d.user(card["user_id"]), sum(c["is_achieved"] == "1" for c in cs.values())]
        for p in range(1, max(n, 16) + 1):
            c = cs.get(p)
            row.append("" if not c else ("（まだ開いていない）" if not c["booth_id"] else
                       f"{'✓ ' if c['is_achieved'] == '1' else ''}{d.booth(c['booth_id'])}（{ja(c['source']) or '—'}）"))
        rows.append(row)
    rows.sort(key=lambda r: -r[1])
    sheet(wb, "11.ビンゴカード", ["名前", "開いたマス数"] + [f"マス{i}" for i in range(1, 17)], rows,
          widths=[20, 10] + [28] * 16, wrap=tuple(range(3, 19)))

    # 12. アンケート回答
    keys = [q["question_key"] for q in d.questions]
    rows = []
    for s in d.t["user_survey_answers"]:
        a = d.answers(s["user_id"])
        rows.append([d.user(s["user_id"])] + [d.answer_text(k, a.get(k)) for k in keys])
    sheet(wb, "12.アンケート回答", ["名前"] + [q["question_text"] for q in d.questions], rows, widths=[20] + [26] * len(keys), wrap=tuple(range(2, len(keys) + 2)))

    # 13. 時間帯ごとの動き
    hours = defaultdict(Counter)
    for c in d.t["check_ins"]:
        hours[d.jst("check_ins", "checked_in_at", c["checked_in_at"]).hour]["訪問"] += 1
    for r in rated:
        hours[d.jst("booth_ratings", "rated_at", r["rated_at"]).hour]["評価"] += 1
    for v in votes:
        hours[d.jst("award_votes", "created_at", v["created_at"]).hour]["投票"] += 1
    for g in gacha:
        hours[d.jst("gacha_coin_uses", "used_at", g["used_at"]).hour]["ガチャ"] += 1
    for r in d.recs:
        hours[d.jst("recommendation_scores", "created_at", r["created_at"]).hour]["おすすめ"] += 1
    rows = [[f"{h}時台", c["訪問"], c["評価"], c["おすすめ"], c["投票"], c["ガチャ"]] for h, c in sorted(hours.items())]
    sheet(wb, "13.時間帯ごとの動き", ["時間帯", "ブース訪問", "評価", "おすすめ表示", "アワード投票", "ガチャ"], rows, widths=[10, 11, 8, 12, 12, 8])

    # 14. 用語と注意
    notes = [
        ("時刻", "すべて日本時間です。"),
        ("訪問・チェックイン", "参加者がブースでQRコードなどを読み取って記録した回数です。同じブースに2回行けば2回です。"),
        ("評価", "訪問後に付ける★（4段階）です。コメントは任意です。"),
        ("おすすめ", "アプリがビンゴのマスとして参加者に提示したブースです。「行った」は、時刻に関係なく、その人がそのブースを訪問した記録があることを指します。"),
        ("おすすめの選び方", "；".join(f"{v}" for v in STRATEGY.values())),
        ("ビンゴ", "1人1枚のカード（16マス）。✓ は開いた（達成した）マスです。"),
        ("ガチャ", "ビンゴの列がそろうともらえるコインを使って回したものです。"),
        ("名前", "アプリに登録された表示名です。同じ名前の人が複数いる場合は、登録順に「（2）」「（3）」を付けて区別しています。"),
        ("取り扱い注意", "名前・メールアドレス・コメントなどの個人情報を含みます。共有範囲に注意してください。"),
        ("元データ", "本番データベースの写し（2026-10-19 取得）から作成。数値の集計・表示名への置き換え・時刻の日本時間への統一だけを行っています。"),
    ]
    sheet(wb, "14.用語と注意", ["項目", "説明"], notes, widths=[22, 110], wrap=(2,))

    wb.save(path)
    os.chmod(path, 0o600)
    return {ws.title: ws.max_row - 1 for ws in wb.worksheets}


def main(dump: Path):
    d = Data(dump)
    out = dump / "export"
    out.mkdir(exist_ok=True)
    os.chmod(out, 0o700)
    path = out / "イベントまとめ_非エンジニア向け_20261019.xlsx"
    counts = build(d, path)
    for k, v in counts.items():
        print(f"  {k}: {v} 行")
    print(f"出力: {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
