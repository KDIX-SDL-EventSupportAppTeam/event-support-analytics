"""研究分析: イベント後アンケート（q1〜q5）× アプリの行動データ。SQL 実験室の「研究分析」ページ。

定義は export_research_xlsx.py（推薦評価_研究データ）に合わせる:
- 対象は role が participant または exhibitor（固定。出展者も来場者として回るため合算）。当日（JST 2026-10-16 以降）だけ
- 時刻は JST に揃える。UTC 保存の列（check_ins.checked_in_at / bingo_cells.assigned_at）だけ +9h
- 推薦枠 = recommendation_scores.was_assigned = 1。戦略は reason_payload.strategy、無ければ PRESURVEY 枠 or 解放側の strategy
- 訪問 = 解放時刻以降に同じブースへチェックイン。高評価 = ★4、セレンディピティ = MISMATCH かつ訪問かつ★4
- 推薦の主要 4 戦略（RANDOM / DRSA / SER / EXPLORE）だけで「その人が受けた推薦の内訳」を作る（PRESURVEY・FALLBACK は除く）

アンケートの列（呼び名は分析用。保存列とは質問文で対応付ける）:
q1 ビンゴで訪問数が変わったか（1 思わない〜4 思う）/ q2 開いたマスに行きたくなったか（4 段階 + 覚えていない）/
q3 予想外だが行ってよかったブース（1 なかった〜4 あった）/ q4 QR チェックイン数の自己申告（帯）/
q5 もともと行く予定はなかったが興味を惹かれたブース（1 なし〜4 あり）。自由記述は扱わない
"""

import json
import re
from pathlib import Path

import duckdb
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

DAY0 = "2026-10-16"
ROLES = ("participant", "exhibitor")
STRATEGIES = ["RANDOM", "DRSA", "SER", "EXPLORE"]
STRATEGY_JA = {"RANDOM": "RANDOM（一様乱数）", "DRSA": "DRSA（規則でスコア）",
               "SER": "SER（関心外の規則）", "EXPLORE": "EXPLORE（関心外の探索）"}
QUESTIONS = {
    "q1": "ビンゴで訪問数が変わったと思うか",
    "q2": "新しく開いたマスのブースに行きたくなったか",
    "q3": "予想外だが行ってよかったブースがあったか",
    "q4": "QR でチェックインした数（自己申告）",
    "q5": "予定外だが興味を惹かれたブースがあったか",
}
# 設問はスプレッドシートの列順ではなく、質問文のキーワードで特定する（列順が変わっても取り違えない）
QUESTION_KEYWORDS = {"q1": "ビンゴ機能の影響", "q2": "新しく開いたマス", "q3": "予想外", "q4": "QRコード",
                     "q5": "興味を惹かれた"}
# 均等目盛（1〜4）の設問は、スプレッドシートに数字だけが入る。両端の意味を付けて表示する
SCALE_ENDS = {"q1": ("思わない", "思う"), "q3": ("なかった", "あった"), "q5": ("なし", "あり")}
SCALE_LABEL = {q: {"1": f"1（{lo}）", "2": "2", "3": "3", "4": f"4（{hi}）"} for q, (lo, hi) in SCALE_ENDS.items()}
# 選択肢の既定の並び（画面の「選択肢の並び順」で上書きできる）。肯定・多い側を下に置く元の並び
DEFAULT_ORDERS = {
    "q2": ["とても思わない", "思わない", "思う", "とても思う", "マスが開く前に終わった/覚えていない"],
    "q4": ["0", "1", "2~4", "5~9", "10以上", "覚えていない"],
    **{q: list(m.values()) for q, m in SCALE_LABEL.items()},
}
# 「覚えていない」などは順序のある尺度の外。相関や数値化から外す
NON_SUBSTANTIVE = re.compile(r"覚えていない|終わった|わからない|分からない")
KIND_JA = {"CENTER": "カード（真ん中）", "OUTER": "カード（外側）", "OFF": "カード外"}
NO_ANSWER = "（未回答）"

SQL = f"""
CREATE OR REPLACE TEMP VIEW r_participants AS
  SELECT id AS user_id, role FROM users WHERE role IN {ROLES};

CREATE OR REPLACE TEMP VIEW r_checkins AS
  SELECT c.id, c.user_id, c.booth_id, c.checkin_method, c.checked_in_at + INTERVAL 9 HOUR AS t
  FROM check_ins c JOIN r_participants USING (user_id)
  WHERE c.checked_in_at + INTERVAL 9 HOUR >= TIMESTAMP '{DAY0}';

-- 1 人のカードに同じブースが複数マスあれば、最初に入ったマスを採る
CREATE OR REPLACE TEMP VIEW r_card_booths AS
  SELECT b.user_id, c.booth_id,
         arg_min(c.zone, c.assigned_at) AS zone,
         min(c.assigned_at) + INTERVAL 9 HOUR AS assigned_t
  FROM bingo_cells c JOIN bingo_cards b ON b.id = c.card_id
  WHERE c.booth_id IS NOT NULL
  GROUP BY ALL;

CREATE OR REPLACE TEMP VIEW r_visits AS
  SELECT v.*, coalesce(cb.zone, 'OFF') AS kind,
         cb.assigned_t IS NOT NULL AND cb.assigned_t <= v.t AS on_card_before_visit
  FROM r_checkins v LEFT JOIN r_card_booths cb USING (user_id, booth_id);

CREATE OR REPLACE TEMP VIEW r_slots AS
  WITH s AS (
    SELECT s.id AS slot_id, s.user_id, s.booth_id, s.interest_match, u.phase, u.created_at AS unlock_t,
           coalesce(s.reason_payload->>'strategy',
                    CASE WHEN u.phase = 'PRESURVEY' THEN 'PRESURVEY' ELSE u.strategy END) AS strategy
    FROM recommendation_scores s
    JOIN card_unlock_events u ON u.id = s.unlock_event_id
    JOIN r_participants p ON p.user_id = s.user_id
    WHERE s.was_assigned = 1 AND u.created_at >= TIMESTAMP '{DAY0}'
  ), hit AS (
    SELECT s.slot_id, min(v.t) AS visit_t, arg_min(v.id, v.t) AS visit_checkin_id
    FROM s JOIN r_checkins v ON v.user_id = s.user_id AND v.booth_id = s.booth_id AND v.t >= s.unlock_t
    GROUP BY 1
  )
  SELECT s.*, h.visit_t, h.visit_checkin_id, h.visit_t IS NOT NULL AS visited,
         date_diff('second', s.unlock_t, h.visit_t) / 60.0 AS minutes_to_visit,
         r.rating,
         coalesce(r.rating = 4, false) AS star4,
         s.interest_match = 'MISMATCH' AND h.visit_t IS NOT NULL AND coalesce(r.rating = 4, false) AS serendipity
  FROM s LEFT JOIN hit h USING (slot_id)
  LEFT JOIN (SELECT checkin_id, max(rating) AS rating FROM booth_ratings GROUP BY 1) r ON r.checkin_id = h.visit_checkin_id;
"""


# ---------------------------------------------------------------- データ

def norm_option(s) -> str:
    """全角・波ダッシュの揺れをそろえる（「2〜4」「２～４」→「2~4」）。"""
    return str(s).strip().translate(str.maketrans("０１２３４５６７８９〜～－", "0123456789~~-"))


def q4_band(label) -> tuple[float, float] | None:
    """q4 の選択肢 → 件数の範囲。「2~4」→ (2, 4)、「10以上」→ (10, inf)、「覚えていない」→ None。"""
    t = norm_option(label)
    if NON_SUBSTANTIVE.search(t):
        return None
    nums = [float(x) for x in re.findall(r"\d+", t)]
    if not nums:
        return None
    if "以上" in t:
        return (nums[0], float("inf"))
    return (nums[0], nums[1]) if len(nums) >= 2 else (nums[0], nums[0])


def band_of(n: float, bands: dict[str, tuple[float, float]]) -> str | None:
    return next((lab for lab, (lo, hi) in bands.items() if lo <= n <= hi), None)


def build(con: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    for stmt in SQL.split(";"):
        if stmt.strip():
            con.execute(stmt)
    visits = con.execute("""SELECT v.*, r.rating FROM r_visits v
                            LEFT JOIN (SELECT checkin_id, max(rating) AS rating FROM booth_ratings GROUP BY 1) r ON r.checkin_id = v.id""").df()
    slots = con.execute("SELECT * FROM r_slots").df()
    users = con.execute("""
        SELECT p.user_id, p.role, a.age_range, a.occupation
        FROM r_participants p LEFT JOIN user_survey_answers a USING (user_id)""").df().drop_duplicates("user_id")
    extra = con.execute("""
        SELECT p.user_id,
          (SELECT count(*) FROM bingo_cells c JOIN bingo_cards b ON b.id = c.card_id
             WHERE b.user_id = p.user_id AND c.is_achieved = 1) AS bingo_achieved,
          (SELECT count(*) FROM gacha_coin_uses g WHERE g.user_id = p.user_id) AS gacha_coins,
          (SELECT count(*) FROM booth_ratings r WHERE r.user_id = p.user_id) AS ratings
        FROM r_participants p""").df()
    qmap = question_map(con)
    if qmap:
        survey = con.execute("SELECT user_id, " + ", ".join(f'"{k}" AS {q}' for q, (k, _) in qmap.items())
                             + " FROM post_survey").df()
        for q in QUESTIONS:
            if q not in survey:  # 見つからない設問は空（0 概要の対応表に「見つからない」と出る）
                survey[q] = None
            survey[q] = survey[q].map(lambda v, q=q: SCALE_LABEL.get(q, {}).get(norm_option(v), norm_option(v))
                                      if pd.notna(v) and str(v).strip() else None)
    else:  # 未取り込み
        survey = pd.DataFrame(columns=["user_id", *QUESTIONS])

    u = users.merge(extra, on="user_id", how="left")
    g = visits.groupby("user_id")
    u = u.merge(pd.DataFrame({
        "checkins": g.size(), "unique_booths": g.booth_id.nunique(),
        "qr_checkins": g.checkin_method.apply(lambda s: (s == "qr").sum()),
        "first_t": g.t.min(), "last_t": g.t.max(),
        **{f"visits_{k}": g.kind.apply(lambda s, k=k: (s == k).sum()) for k in KIND_JA},
        "card_visits_after_assigned": g.on_card_before_visit.sum(),
    }).reset_index(), on="user_id", how="left")
    # 最初の推薦（PRESURVEY 以外）の解放前のチェックイン数 = ビンゴの推薦が効く前のペース
    first_unlock = slots[slots.strategy != "PRESURVEY"].groupby("user_id").unlock_t.min().rename("first_unlock_t")
    pre = visits.merge(first_unlock, on="user_id", how="left")
    pre = pre[pre.first_unlock_t.isna() | (pre.t < pre.first_unlock_t)].groupby("user_id").size().rename("checkins_before_first_rec")
    u = u.merge(pre.reset_index(), on="user_id", how="left")

    main = slots[slots.strategy.isin(STRATEGIES)]
    sg = main.groupby("user_id")
    mix = pd.DataFrame({"rec_slots": sg.size(), "rec_visits": sg.visited.sum(),
                        "star4_visits": sg.star4.sum(), "serendipity": sg.serendipity.sum()})
    for s in STRATEGIES:
        m = main[main.strategy == s].groupby("user_id")
        mix[f"slots_{s}"] = m.size()
        mix[f"visits_{s}"] = m.visited.sum()
    mix = mix.fillna(0)
    for s in STRATEGIES:
        mix[f"share_{s}"] = mix[f"slots_{s}"] / mix.rec_slots
    mix["rec_visit_rate"] = mix.rec_visits / mix.rec_slots
    mix["main_strategy"] = mix[[f"share_{s}" for s in STRATEGIES]].idxmax(axis=1).str.removeprefix("share_")
    u = u.merge(mix.reset_index(), on="user_id", how="left")

    num_cols = [c for c in u.columns if c.startswith(("checkins", "qr_checkins", "visits_", "card_visits", "rec_slots", "rec_visits",
                                                       "slots_", "star4", "serendipity", "unique_booths"))]
    u[num_cols] = u[num_cols].fillna(0)
    u["hours_active"] = (u.last_t - u.first_t).dt.total_seconds() / 3600
    u["off_card_share"] = u.visits_OFF / u.checkins.where(u.checkins > 0)

    u = u.merge(survey, on="user_id", how="left")
    u["responded"] = u.user_id.isin(survey.user_id)
    for q in QUESTIONS:
        u[q] = u[q].fillna(NO_ANSWER).replace("", NO_ANSWER)
    # q4 は「QR で」読み取った数を聞いているので、比べる相手は QR のチェックインだけ（コード入力は含めない）
    bands = {lab: b for lab in DEFAULT_ORDERS["q4"] + sorted(u.q4.unique()) if (b := q4_band(lab))}
    u["q4_lo"] = u.q4.map(lambda a: (q4_band(a) or (None, None))[0])
    u["q4_hi"] = u.q4.map(lambda a: (q4_band(a) or (None, None))[1])
    u["qr_band"] = u.qr_checkins.map(lambda n: band_of(n, bands))
    u["q4_match"] = u.q4_lo.notna() & (u.qr_checkins >= u.q4_lo) & (u.qr_checkins <= u.q4_hi)
    # 帯の外なら、近いほうの端までの距離（帯の中なら 0）
    u["q4_gap"] = (u.q4_lo - u.qr_checkins).clip(lower=0) - (u.qr_checkins - u.q4_hi).clip(lower=0)
    u.loc[u.q4_lo.isna(), "q4_gap"] = None
    pos = {lab: i for i, lab in enumerate(bands)}
    u["q4_band_dist"] = [abs(pos[a] - pos[b]) if a in pos and b in pos else None for a, b in zip(u.q4, u.qr_band)]

    slots = slots.merge(u[["user_id", "q1", "q2", "q3", "responded"]], on="user_id", how="left")
    visits = visits.merge(u[["user_id", "q1", "responded"]], on="user_id", how="left")
    return {"users": u, "slots": slots, "visits": visits}


def question_map(con: duckdb.DuckDBPyConnection) -> dict[str, tuple[str, str]]:
    """q1..q4（分析での呼び名）→ (post_survey の列, 質問文)。質問文のキーワードで探す。"""
    try:
        qs = con.execute('SELECT q_key, question FROM post_survey_questions ORDER BY "order"').df()
    except duckdb.Error:
        return {}
    out = {}
    for q, kw in QUESTION_KEYWORDS.items():
        hit = qs[qs.question.str.replace(r"\s", "", regex=True).str.contains(kw, regex=False)]
        if len(hit):
            out[q] = (hit.q_key.iloc[0], hit.question.iloc[0])
    return out


def ordinal(series: pd.Series, order: list[str]) -> pd.Series:
    """選択肢 → 並び順の番号。「覚えていない」などは欠損にする。"""
    rank = {a: i for i, a in enumerate(x for x in order if not NON_SUBSTANTIVE.search(x))}
    return series.map(rank)


# ---------------------------------------------------------------- 回答の並び順

def order_path(root: Path) -> Path:
    return root / "survey" / "answer_order.json"


def answer_orders(root: Path, u: pd.DataFrame) -> dict[str, list[str]]:
    saved = {}
    if order_path(root).exists():
        saved = json.loads(order_path(root).read_text(encoding="utf-8"))
    out = {}
    for q in QUESTIONS:
        seen = [a for a in u.loc[u.responded, q].unique() if a != NO_ANSWER]
        # 既定の選択肢は回答 0 人でも残す（各選択肢の回答数に 0 と出すため）
        known = [a for a in saved.get(q, DEFAULT_ORDERS.get(q, [])) if a in seen or a in DEFAULT_ORDERS.get(q, [])]
        rest = sorted(a for a in seen if a not in known)
        # 「覚えていない」などは末尾に
        out[q] = [a for a in known + rest if not NON_SUBSTANTIVE.search(a)] + [a for a in known + rest if NON_SUBSTANTIVE.search(a)]
    return out


def order_editor(root: Path, orders: dict[str, list[str]]) -> None:
    with st.expander("選択肢の並び順（グラフの並び。上から「肯定的」→「否定的」など）"):
        new = {}
        cols = st.columns(len(QUESTIONS))
        for c, q in zip(cols, QUESTIONS):
            txt = c.text_area(f"{q}: {QUESTIONS[q]}", "\n".join(orders[q]), height=160, key=f"order_{q}")
            new[q] = [x.strip() for x in txt.splitlines() if x.strip()]
        if st.button("並び順を保存"):
            p = order_path(root)
            p.parent.mkdir(mode=0o700, exist_ok=True)
            p.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
            st.rerun()


# ---------------------------------------------------------------- 統計（scipy なし。ブートストラップは乱数を固定して再現可能に）

RNG_SEED = 20261016
N_BOOT = 2000


def _rank(a: np.ndarray) -> np.ndarray:
    return pd.Series(a).rank().to_numpy()


def spearman_ci(x: pd.Series, y: pd.Series) -> tuple[float | None, float | None, float | None, int]:
    """Spearman の順位相関 ρ と 95% ブートストラップ信頼区間（パーセンタイル法）。戻り値: (ρ, 下限, 上限, n)"""
    d = pd.concat([x, y], axis=1).dropna().to_numpy(dtype=float)
    d = d[np.lexsort((d[:, 1], d[:, 0]))]  # 行の順に結果が左右されないように並べ替える（再現性）
    n = len(d)
    if n < 5 or np.ptp(d[:, 0]) == 0 or np.ptp(d[:, 1]) == 0:
        return None, None, None, n
    rho = np.corrcoef(_rank(d[:, 0]), _rank(d[:, 1]))[0, 1]
    rng = np.random.default_rng(RNG_SEED)
    bs = []
    for _ in range(N_BOOT):
        s = d[rng.integers(0, n, n)]
        if np.ptp(s[:, 0]) and np.ptp(s[:, 1]):
            bs.append(np.corrcoef(_rank(s[:, 0]), _rank(s[:, 1]))[0, 1])
    lo, hi = np.percentile(bs, [2.5, 97.5])
    return float(rho), float(lo), float(hi), n


def mean_ci(v: pd.Series) -> tuple[float | None, float | None, float | None, int]:
    a = np.sort(v.dropna().to_numpy(dtype=float))  # 並べ替えて再現性を保つ
    n = len(a)
    if n == 0:
        return None, None, None, 0
    if n == 1:
        return float(a[0]), None, None, 1
    rng = np.random.default_rng(RNG_SEED)
    bs = a[rng.integers(0, n, (N_BOOT, n))].mean(axis=1)
    lo, hi = np.percentile(bs, [2.5, 97.5])
    return float(a.mean()), float(lo), float(hi), n


def diff_ci(a: pd.Series, b: pd.Series) -> tuple[float | None, float | None, float | None]:
    """平均の差（a − b）と 95% ブートストラップ信頼区間。"""
    x, y = np.sort(a.dropna().to_numpy(dtype=float)), np.sort(b.dropna().to_numpy(dtype=float))
    if len(x) < 2 or len(y) < 2:
        return None, None, None
    rng = np.random.default_rng(RNG_SEED)
    bs = x[rng.integers(0, len(x), (N_BOOT, len(x)))].mean(1) - y[rng.integers(0, len(y), (N_BOOT, len(y)))].mean(1)
    lo, hi = np.percentile(bs, [2.5, 97.5])
    return float(x.mean() - y.mean()), float(lo), float(hi)


def sd_ci(v: pd.Series) -> tuple[float | None, float | None, float | None, int]:
    """標準偏差（ばらつき）と 95% ブートストラップ信頼区間。"""
    a = np.sort(v.dropna().to_numpy(dtype=float))  # 並べ替えて再現性を保つ
    n = len(a)
    if n < 3:
        return None, None, None, n
    rng = np.random.default_rng(RNG_SEED)
    bs = a[rng.integers(0, n, (N_BOOT, n))].std(axis=1, ddof=1)
    lo, hi = np.percentile(bs, [2.5, 97.5])
    return float(a.std(ddof=1)), float(lo), float(hi), n


def sd_diff_ci(a: pd.Series, b: pd.Series) -> tuple[float | None, float | None, float | None]:
    x, y = np.sort(a.dropna().to_numpy(dtype=float)), np.sort(b.dropna().to_numpy(dtype=float))
    if len(x) < 3 or len(y) < 3:
        return None, None, None
    rng = np.random.default_rng(RNG_SEED)
    bs = x[rng.integers(0, len(x), (N_BOOT, len(x)))].std(1, ddof=1) - y[rng.integers(0, len(y), (N_BOOT, len(y)))].std(1, ddof=1)
    lo, hi = np.percentile(bs, [2.5, 97.5])
    return float(x.std(ddof=1) - y.std(ddof=1)), float(lo), float(hi)


def wilson(k: int, n: int) -> tuple[float | None, float | None, float | None]:
    """割合の 95% 信頼区間（Wilson 法）。"""
    if n == 0:
        return None, None, None
    z, p = 1.96, k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / den
    return p, c - h, c + h


def smd(a: pd.Series, b: pd.Series) -> float | None:
    """標準化平均差（2 群の平均差 ÷ プールした標準偏差）。|0.1| 未満 ≒ 無視できる、0.2 小、0.5 中、0.8 大。"""
    a, b = a.dropna(), b.dropna()
    if len(a) < 2 or len(b) < 2:
        return None
    sd = ((a.var() + b.var()) / 2) ** 0.5
    return float((a.mean() - b.mean()) / sd) if sd else None


def fmt_rho(r) -> str:
    rho, lo, hi, n = r
    return "算出不可（回答のばらつき不足）" if rho is None else f"ρ = {rho:+.2f}［95%CI {lo:+.2f}, {hi:+.2f}］, n = {n}"


def rho_words(r) -> str:
    """順位相関の結果を、研究者がそのまま書ける一文にする。"""
    rho, lo, hi, n = r
    if rho is None:
        return "回答のばらつきが足りず、関連を推定できない"
    sig = lo > 0 or hi < 0
    if abs(rho) < 0.1:
        return f"関連はほぼ無い（{fmt_rho(r)}）"
    size = "弱い" if abs(rho) < 0.3 else "中程度の" if abs(rho) < 0.5 else "強い"
    sign = "正の" if rho > 0 else "負の"
    sure = "信頼区間が 0 を含まず、偶然とは考えにくい" if sig else "ただし信頼区間が 0 をまたぐため、この標本では関連があるとは言えない"
    return f"{sign}{size}関連（{fmt_rho(r)}）。{sure}"


# ---------------------------------------------------------------- 見た目（dataviz の既定パレット。ライト/ダークで段を変える）

def palette() -> dict[str, str]:
    try:
        dark = st.context.theme.type == "dark"
    except Exception:
        dark = False
    if dark:
        return {"ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a", "axis": "#383835",
                "neutral": "#383835", "s1": "#3987e5", "s2": "#d95926", "s3": "#199e70", "s4": "#c98500",
                "pos2": "#3987e5", "pos1": "#1c5cab", "neg1": "#8f3a3a", "neg2": "#e66767", "band": "rgba(255,255,255,0.06)"}
    return {"ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9", "axis": "#c3c2b7",
            "neutral": "#d6d5cf", "s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a", "s4": "#eda100",
            "pos2": "#2a78d6", "pos1": "#86b6ef", "neg1": "#f1a3a2", "neg2": "#e34948", "band": "rgba(11,11,11,0.05)"}


def strategy_colors(P) -> dict[str, str]:
    return dict(zip(STRATEGIES, [P["s1"], P["s2"], P["s3"], P["s4"]]))


def style(fig: go.Figure, P: dict, height: int, title: str | None = None, subtitle: str | None = None) -> go.Figure:
    t = None
    if title:
        t = f"<b>{title}</b>" + (f"<br><span style='font-size:12px;color:{P['ink2']}'>{subtitle}</span>" if subtitle else "")
    fig.update_layout(height=height, title=dict(text=t, x=0, xanchor="left", font=dict(size=15, color=P["ink"])),
                      margin=dict(t=75 if subtitle else 50, l=10, r=20, b=10), font=dict(color=P["ink2"], size=12),
                      plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", bargap=0.35,
                      # 凡例は図の下（タイトル・補足と重ならないように）
                      legend=dict(orientation="h", yanchor="top", y=-75 / height, x=0, title_text="", traceorder="normal"),
                      hoverlabel=dict(font_size=12))
    fig.update_xaxes(gridcolor=P["grid"], zerolinecolor=P["axis"], linecolor=P["axis"], title_font_size=12, automargin=True)
    fig.update_yaxes(gridcolor=P["grid"], zerolinecolor=P["axis"], linecolor=P["axis"], title_font_size=12, automargin=True)
    return fig


def plot(fig: go.Figure) -> None:
    st.plotly_chart(fig, width="stretch", theme=None, config={"displaylogo": False})


QSHORT = {"q1": "q1 訪問数が変わった", "q2": "q2 行きたくなった", "q3": "q3 予想外によかった", "q4": "q4 QR の数",
          "q5": "q5 予定外に興味を惹かれた"}


def qlab(q: str) -> str:
    return f"{q}: {QUESTIONS[q]}" if q in QUESTIONS else q


def substantive(order: list[str]) -> list[str]:
    return [a for a in order if not NON_SUBSTANTIVE.search(a)]


# ---------------------------------------------------------------- 文章の枠

def rq_intro(question: str, why: str, how: str) -> None:
    with st.container(border=True):
        st.markdown(f"**問い** — {question}")
        st.markdown(f"**なぜ確かめるか** — {why}")
        st.markdown(f"**どう確かめるか** — {how}")


def finding(lines: list[str]) -> None:
    st.markdown("##### 結果")
    st.success("\n".join(f"- {x}" for x in lines))


def reading(cases: list[tuple[str, str]], caveat: str | None = None) -> None:
    with st.expander("図の読み方と、結果ごとの解釈", expanded=False):
        st.markdown("\n".join(f"- **{a}** → {b}" for a, b in cases))
        if caveat:
            st.caption("限界: " + caveat)


# ---------------------------------------------------------------- 図の部品

def likert_fig(r: pd.DataFrame, qs: list[str], orders: dict, P: dict) -> go.Figure:
    """4 段階の設問を、中央（2 と 3 の間）で左右に分けた積み上げ横棒にする。左 = 否定側、右 = 肯定側。"""
    colors = [P["neg2"], P["neg1"], P["pos1"], P["pos2"]]
    fig = go.Figure()
    rows = [qlab(q) for q in qs]
    for k in range(4):
        xs, texts, hov = [], [], []
        for q in qs:
            opts = substantive(orders[q])[:4]
            base = r[q].isin(opts)
            n = int(base.sum())
            share = (r.loc[base, q] == opts[k]).mean() if n and k < len(opts) else 0
            xs.append(-share if k < 2 else share)
            texts.append(f"{share:.0%}" if share >= 0.06 else "")
            hov.append(f"{opts[k] if k < len(opts) else ''}: {share:.1%}（{int(round(share * n))} / {n} 人）")
        fig.add_bar(y=rows, x=xs, orientation="h", marker_color=colors[k], name=["1（否定側）", "2", "3", "4（肯定側）"][k], legendrank=k,
                    text=texts, textposition="inside", insidetextanchor="middle", hovertext=hov, hoverinfo="text",
                    marker_line_color="rgba(0,0,0,0)", textfont=dict(color="#fff" if k in (0, 3) else P["ink"]))
    # 左側（否定）は内側から 2 → 1 の順に積む
    fig.data = (fig.data[1], fig.data[0], fig.data[2], fig.data[3])
    fig.update_layout(barmode="relative")
    fig.add_vline(x=0, line_color=P["ink2"], line_width=1)
    fig.update_xaxes(tickformat=".0%", range=[-1, 1], tickvals=[-1, -.5, 0, .5, 1],
                     ticktext=["100%", "50%", "0", "50%", "100%"], title="← 否定側（1・2）　｜　肯定側（3・4）→")
    fig.update_yaxes(autorange="reversed", type="category")
    return fig


def dot_ci_fig(groups: list[str], stats: list[tuple], P: dict, xlab: str, ylab: str, color=None, fmt="{:.1f}") -> go.Figure:
    """グループごとの平均（点）と 95% 信頼区間（ひげ）。n を軸ラベルに入れる。"""
    color = color or P["s1"]
    m = [s[0] for s in stats]
    lo = [None if s[1] is None else s[0] - s[1] for s in stats]
    hi = [None if s[2] is None else s[2] - s[0] for s in stats]
    labels = [f"{g}<br><span style='font-size:11px'>n={s[3]}</span>" for g, s in zip(groups, stats)]
    fig = go.Figure(go.Scatter(
        x=labels, y=m, mode="markers+text", marker=dict(size=11, color=color),
        error_y=dict(type="data", symmetric=False, array=hi, arrayminus=lo, color=color, thickness=2, width=6),
        text=[fmt.format(v) if v is not None else "" for v in m], textposition="middle right",
        textfont=dict(color=P["ink"]), hovertext=[f"{g}: 平均 {fmt.format(s[0]) if s[0] is not None else '-'}"
                                                  + (f"（95%CI {fmt.format(s[1])}–{fmt.format(s[2])}）" if s[1] is not None else "")
                                                  + f", n={s[3]}" for g, s in zip(groups, stats)], hoverinfo="text"))
    fig.update_xaxes(type="category", title=xlab)
    fig.update_yaxes(title=ylab, rangemode="tozero")
    return fig


def forest_fig(rows: list[str], cols: list[str], res: dict, P: dict, titles: list[str] | None = None) -> go.Figure:
    """フォレストプロット: 行 = 推薦の戦略、列 = 設問。点 = ρ、横線 = 95% 信頼区間、縦線 = 0（関連なし）。"""
    from plotly.subplots import make_subplots
    fig = make_subplots(rows=1, cols=len(cols), shared_yaxes=True, horizontal_spacing=0.04,
                        subplot_titles=titles or cols)
    for j, c in enumerate(cols, 1):
        for i, rname in enumerate(rows):
            rho, lo, hi, n = res[(rname, c)]
            if rho is None:
                continue
            sig = lo > 0 or hi < 0
            col = P["s1"] if sig else P["muted"]
            fig.add_trace(go.Scatter(x=[lo, hi], y=[rname, rname], mode="lines", line=dict(color=col, width=2),
                                     hoverinfo="skip", showlegend=False), 1, j)
            fig.add_trace(go.Scatter(x=[rho], y=[rname], mode="markers+text", marker=dict(size=10, color=col),
                                     text=[f"{rho:+.2f}"], textposition="top center", textfont=dict(color=P["ink"], size=11),
                                     hovertext=[f"{rname} × {c}<br>{fmt_rho((rho, lo, hi, n))}"], hoverinfo="text",
                                     showlegend=False), 1, j)
        fig.add_vline(x=0, line_color=P["ink2"], line_width=1, row=1, col=j)
        fig.update_xaxes(range=[-1, 1], tickvals=[-1, -.5, 0, .5, 1], row=1, col=j)
    fig.update_yaxes(type="category", autorange="reversed")
    for a in fig.layout.annotations:
        a.font = dict(size=12, color=P["ink"])
        a.y = 1.0
    return fig


# ---------------------------------------------------------------- ページ

CHECKIN_BINS = [(0, 0, "0 件"), (1, 4, "1–4 件"), (5, 9, "5–9 件"), (10, 14, "10–14 件"), (15, 10**9, "15 件以上")]
BIAS_VARS = {"checkins": "チェックイン数", "rec_slots": "受けた推薦枠の数", "rec_visit_rate": "推薦先の訪問率",
             "bingo_achieved": "達成したマス数", "ratings": "ブースを評価した数", "gacha_coins": "使ったガチャコイン"}


def checkin_bin(n: float) -> str:
    return next(label for lo, hi, label in CHECKIN_BINS if lo <= n <= hi)


def page(con: duckdb.DuckDBPyConnection, root: Path) -> None:
    P = palette()
    st.title("イベント後アンケート × 行動ログの分析")
    d = build(con)
    u, slots, visits = d["users"], d["slots"], d["visits"]
    r = u[u.responded]
    if r.empty:
        missing = [f"{q}（「{QUESTION_KEYWORDS[q]}」を含む質問）" for q in QUESTIONS if q not in question_map(con)]
        st.warning("アンケートが未取り込みか、設問が見つかりません。"
                   + (f"見つからない: {', '.join(missing)}" if missing else "「アンケート取り込み」で取り込んでください"))
        return
    orders = answer_orders(root, u)
    active = u[u.checkins > 0]

    st.markdown(
        "この分析の目的は、**ビンゴ形式の推薦が来場者の行動と体験をどう変えたか**を、"
        "本人の主観（アンケート）と実際の行動（アプリのログ）の両方から確かめることである。"
        "主観だけでは思い込みと区別できず、ログだけでは本人にとっての意味がわからない。両者を同じ人で突き合わせる。")
    c = st.columns(4)
    c[0].metric("分析対象（参加者 + 出展者）", f"{len(u)} 人")
    c[1].metric("アプリを使った人（チェックイン 1 件以上）", f"{len(active)} 人")
    c[2].metric("アンケート回答者", f"{len(r)} 人")
    c[3].metric("アプリ利用者に占める回答率", f"{r.checkins.gt(0).sum() / max(len(active), 1):.0%}")
    st.caption("当日（JST 10/16）のデータのみ。訪問 = 推薦のマスが開いた後に、そのブースへチェックインしたこと。"
               "セレンディピティ = 推薦時点で本人の関心外（MISMATCH）だったブースに行き、★4 を付けたこと。"
               "信頼区間（95%CI）は、ブートストラップ法（2,000 回）で求めた。")

    with st.expander("設定: 設問の対応・選択肢の並び"):
        qmap = question_map(con)
        st.dataframe(pd.DataFrame([{"呼び名": q, "内容": QUESTIONS[q], "保存列": qmap.get(q, ("見つからない", ""))[0],
                                    "質問文": qmap.get(q, ("", "—"))[1]} for q in QUESTIONS]), hide_index=True)
        order_editor(root, orders)

    tabs = st.tabs(["📌 知りたいことへの答え", "① 回答の概要", "② 回答者は誰か", "③ 自己申告は正確か", "④ ビンゴは回遊を増やしたか",
                    "⑤ 推薦方式と体験", "⑥ 主観と行動は一致するか", "データ表"])
    likert_qs = [q for q in ("q1", "q2", "q3", "q5") if len(substantive(orders[q])) >= 4]

    # ---------------- ★ 問いへの答え
    with tabs[0]:
        answers_tab(u, r, visits, slots, orders, P)
    tabs = tabs[1:]

    # ---------------- ① 概要
    with tabs[0]:
        rq_intro("回答者は各設問にどう答えたか。",
                 "以降の分析はすべて、この回答の違いで人を分けて行う。回答が片側に偏っていると、比べる相手がいなくなる。",
                 "4 段階の設問は、否定側（1・2）を左、肯定側（3・4）を右に積んだ横棒で示す（中央の線より右が長いほど肯定的）。")
        plot(style(likert_fig(r, likert_qs, orders, P), P, 110 + 70 * len(likert_qs),
                   "4 段階の設問への回答の分布", f"回答者 {len(r)} 人。棒にカーソルを乗せると人数"))
        pos = {q: r[q].isin(substantive(orders[q])[2:4]).sum() / max(r[q].isin(substantive(orders[q])[:4]).sum(), 1)
               for q in likert_qs}
        other = {q: int(r[q].isin([a for a in orders[q] if NON_SUBSTANTIVE.search(a)]).sum()) for q in likert_qs}
        finding([f"{qlab(q)} — 肯定側（3・4）が **{pos[q]:.0%}**"
                 + (f"（尺度外の回答 {other[q]} 人を除く）" if other[q] else "") for q in likert_qs])
        q4c = r.q4.value_counts().reindex(orders["q4"]).fillna(0).astype(int)
        fig = go.Figure(go.Bar(x=q4c.index, y=q4c.values, marker_color=P["s1"], text=q4c.values, textposition="outside"))
        fig.update_xaxes(type="category", title="選んだ選択肢")
        fig.update_yaxes(title="人数")
        plot(style(fig, P, 320, qlab("q4"), "件数の帯で答える設問"))
        with st.expander("全設問・全選択肢の人数（表）"):
            st.dataframe(pd.concat([r[q].value_counts().reindex(orders[q]).fillna(0).astype(int)
                                    .rename_axis("選択肢").rename("人数").reset_index().assign(設問=qlab(q),
                                    割合=lambda x: x.人数 / len(r)) for q in QUESTIONS])[["設問", "選択肢", "人数", "割合"]]
                         .style.format({"割合": "{:.1%}"}), hide_index=True)

    # ---------------- ② 代表性
    with tabs[1]:
        rq_intro("アンケートに答えた人は、答えなかった人と比べて偏っていないか。",
                 "回答者がアプリを熱心に使った人に偏っていれば、以降の結果は「熱心な人たち」の話であり、来場者全体には一般化できない。",
                 "行動の指標ごとに標準化平均差（SMD = 平均の差 ÷ ばらつき）を求める。0 から離れるほど差が大きい。"
                 "比較相手は「アプリを使った非回答者」（チェックイン 1 件以上）と「非回答者全員」の 2 通り。")
        nonresp = u[~u.responded]
        rows = []
        for k, lab in BIAS_VARS.items():
            rows.append({"指標": lab, "アプリを使った非回答者と比べて": smd(r[k], nonresp[nonresp.checkins > 0][k]),
                         "非回答者全員と比べて": smd(r[k], nonresp[k])})
        sm = pd.DataFrame(rows)
        fig = go.Figure()
        fig.add_vrect(x0=-0.2, x1=0.2, fillcolor=P["band"], line_width=0)
        for col, color, sym in (("アプリを使った非回答者と比べて", P["s1"], "circle"), ("非回答者全員と比べて", P["muted"], "diamond-open")):
            fig.add_trace(go.Scatter(x=sm[col], y=sm.指標, mode="markers", name=col, marker=dict(size=11, color=color, symbol=sym),
                                     hovertext=[f"{a}: SMD {v:+.2f}" for a, v in zip(sm.指標, sm[col])], hoverinfo="text"))
        fig.add_vline(x=0, line_color=P["ink2"], line_width=1)
        fig.update_xaxes(title="標準化平均差（SMD）　← 非回答者のほうが大きい　｜　回答者のほうが大きい →", zeroline=False)
        fig.update_yaxes(type="category", autorange="reversed")
        plot(style(fig, P, 360, "回答者と非回答者の行動の差", "灰色の帯（±0.2）の内側なら差は小さい"))
        bigdf = sm[sm["アプリを使った非回答者と比べて"].abs() >= 0.2]
        big = [f"{a}（回答者のほうが{'多い' if v > 0 else '少ない'}, SMD {v:+.2f}）"
               for a, v in zip(bigdf.指標, bigdf["アプリを使った非回答者と比べて"])]
        lean = ("回答者はアプリ利用者の中でも熱心な側に寄っている。" if (bigdf["アプリを使った非回答者と比べて"] > 0).all()
                else "回答者はアプリ利用者の中でも控えめな側に寄っている。" if (bigdf["アプリを使った非回答者と比べて"] < 0).all()
                else "差の向きは指標によって異なる。")
        drop = (sm["非回答者全員と比べて"].abs() - sm["アプリを使った非回答者と比べて"].abs()).mean()
        b = u.assign(帯=u.checkins.map(checkin_bin)).groupby("帯").agg(対象=("user_id", "size"), 回答=("responded", "sum"))
        b = b.reindex([lab for *_, lab in CHECKIN_BINS]).fillna(0).astype(int)
        ci = [wilson(int(k), int(n)) for k, n in zip(b.回答, b.対象)]
        finding([
            f"非回答者 {len(nonresp)} 人のうち **{int((nonresp.checkins == 0).sum())} 人はチェックイン 0 件**（アプリを使っていない）。"
            + (f"この人たちを比較から外すと、差（|SMD|）は平均 {drop:.2f} 小さくなる。偏りの一部はアプリを使ったかどうかで説明できる。"
             if drop >= 0.05 else "この人たちを比較から外しても、差はほとんど変わらない。"),
            ("アプリを使った人どうしで比べても差が残る指標（|SMD| ≥ 0.2）: **" + "、".join(big) + "**。" + lean
             if big else "アプリを使った人どうしで比べると、どの指標も |SMD| < 0.2。回答者はアプリ利用者の代表とみなしてよい。"),
            "チェックイン数の帯ごとの回答率: " + "、".join(f"{lab} {p:.0%}" for lab, (p, *_ ) in zip(b.index, ci) if p is not None),
        ])
        fig = dot_ci_fig(list(b.index), [(p, lo, hi, int(n)) for (p, lo, hi), n in zip(ci, b.対象)], P,
                         "当日のチェックイン数", "アンケートの回答率", fmt="{:.0%}")
        fig.update_yaxes(tickformat=".0%", range=[0, 1])
        plot(style(fig, P, 340, "よく回った人ほど回答したか", "点 = 回答率、ひげ = 95% 信頼区間（Wilson 法）"))
        reading([("帯の内側（±0.2）に青い点が収まる", "回答者はアプリ利用者の代表として扱える"),
                 ("灰色（全員と比較）は外、青（利用者と比較）は内", "偏りの正体は「アプリを使ったかどうか」。結論を「アプリ利用者」に限定して書けばよい"),
                 ("青い点も右に外れる", "回答者は熱心な層。行動量に関わる結果（④）は上振れしている前提で読む"),
                 ("回答率が右肩上がり", "よく回った人ほど回答している")],
                "SMD は平均の差なので、外れ値の多い指標では中央値の差も合わせて確認する（データ表）")

    # ---------------- ③ 自己申告
    with tabs[2]:
        rq_intro("本人が答えた QR チェックイン数（q4）は、記録と合っているか。",
                 "自己申告がどれくらい正確かは、q1 など他の主観的な回答をどこまで信用できるかの目安になる。",
                 "記録上の QR チェックイン数（コード入力は除く）を q4 と同じ帯に区切り、本人の申告と照合する。")
        a2 = r[r.q4_lo.notna()]
        if a2.empty:
            st.info("q4 の回答から件数の帯を読み取れませんでした")
        else:
            band_order = [x for x in orders["q4"] if q4_band(x)]
            m1, m2 = wilson(int(a2.q4_match.sum()), len(a2)), wilson(int((a2.q4_band_dist <= 1).sum()), len(a2))
            rr4 = spearman_ci(ordinal(a2.q4, orders["q4"]), a2.qr_checkins)
            ct = pd.crosstab(a2.q4, a2.qr_band).reindex(index=band_order, columns=band_order).fillna(0).astype(int)
            fig = go.Figure(go.Heatmap(z=ct.values, x=ct.columns, y=ct.index, colorscale=[[0, "rgba(0,0,0,0)"], [1, P["s1"]]],
                                       text=ct.values, texttemplate="%{text}", showscale=False,
                                       hovertemplate="申告 %{y} × 記録 %{x}: %{z} 人<extra></extra>"))
            fig.update_xaxes(type="category", title="記録上の QR チェックイン数（同じ帯に区切ったもの）")
            fig.update_yaxes(type="category", title="本人の申告（q4）", autorange="reversed")
            finding([f"申告と記録の帯が一致した人: **{m1[0]:.0%}**（95%CI {m1[1]:.0%}–{m1[2]:.0%}, n = {len(a2)}）",
                     f"一致または隣の帯まで含めると: **{m2[0]:.0%}**（95%CI {m2[1]:.0%}–{m2[2]:.0%}）",
                     f"申告と記録の順位の関連: {rho_words(rr4)}",
                     f"少なく申告した人 {(a2.q4_gap < 0).mean():.0%}、多く申告した人 {(a2.q4_gap > 0).mean():.0%}"
                     f"（「覚えていない」{int(r.q4_lo.isna().sum())} 人は除外）"])
            plot(style(fig, P, 380, "本人の申告 × 記録", "左上から右下への対角線上が一致。対角線より左は申告が多い、右は申告が少ない"))
            reading([("一致率が高く（目安 7 割以上）、隣の帯まで 9 割以上", "自己申告は信頼でき、他の主観的な設問も一定の信頼を置ける"),
                     ("対角線より右（記録のほうが多い）に偏る", "数を少なめに覚えている。主観的な「多い・少ない」も控えめに出やすい"),
                     ("一致率が低い", "主観的な回答の解釈は慎重に。行動ログを主、アンケートを従として扱う")],
                    "帯の境界付近の人は、1 件の違いで不一致になる")

    # ---------------- ④ ビンゴと回遊
    with tabs[3]:
        rq_intro("「ビンゴの影響で訪れるブースの数が変わった」と感じた人（q1）は、実際に多くのブースを回ったか。",
                 "本人の実感が行動に表れていれば、ビンゴが回遊を促したという主張に、主観と客観の両方の裏付けが得られる。",
                 "q1 の回答ごとに、当日のチェックイン数の平均と 95% 信頼区間を比べる。比較対照として、最初の推薦マスが開く前のチェックイン数"
                 "（＝ビンゴの推薦が効く前の、その人のもともとの回り方）も同じように並べる。")
        q1o = substantive(orders["q1"])
        a = r[r.q1.isin(q1o)]
        r_all, r_pre = spearman_ci(ordinal(a.q1, orders["q1"]), a.checkins), spearman_ci(ordinal(a.q1, orders["q1"]), a.checkins_before_first_rec)
        c1, c2 = st.columns(2)
        with c1:
            fig = dot_ci_fig(q1o, [mean_ci(a.loc[a.q1 == o, "checkins"]) for o in q1o], P, qlab("q1"), "チェックイン数（平均）")
            plot(style(fig, P, 360, "当日のチェックイン数", "点 = 平均、ひげ = 95% 信頼区間"))
        with c2:
            fig = dot_ci_fig(q1o, [mean_ci(a.loc[a.q1 == o, "checkins_before_first_rec"]) for o in q1o], P, qlab("q1"),
                             "チェックイン数（平均）", color=P["muted"])
            plot(style(fig, P, 360, "比較: 推薦が始まる前のチェックイン数", "もともとの回り方。ここでも差があれば、q1 は元の熱心さを反映しているだけ"))
        finding([f"q1 と当日のチェックイン数: {rho_words(r_all)}",
                 f"q1 と推薦前のチェックイン数: {rho_words(r_pre)}",
                 ("当日だけに関連が見られ、推薦前には見られない。**ビンゴの推薦が始まってから差が開いた**と解釈できる。"
                  if (r_all[0] or 0) >= 0.1 and (r_pre[0] is None or abs(r_pre[0]) < 0.1) else
                  "推薦前にも同程度の関連がある。q1 の回答は、ビンゴの効果よりも**もともとの回り方**を反映している可能性が高い。"
                  if (r_all[0] or 0) >= 0.1 else "q1 の回答とチェックイン数の間に、はっきりした関連は見られない。")])
        st.markdown("##### どこを回ったか")
        st.caption("チェックインを、カードの真ん中のマスのブース・外側のマスのブース・カードに無いブースに分けた割合（q1 の回答ごと）。"
                   "「思う」側でカードのブースが増えていれば、ビンゴが直接その場所へ誘導したことを示す。")
        rv = visits[visits.responded == True]  # noqa: E712
        fig = go.Figure()
        kinds = [("CENTER", P["s1"]), ("OUTER", P["s3"]), ("OFF", P["neutral"])]
        n_by = rv[rv.q1.isin(q1o)].groupby("q1").size()
        for k, col in kinds:
            share = [(rv[(rv.q1 == o)].kind == k).mean() if n_by.get(o, 0) else 0 for o in q1o]
            fig.add_bar(y=[f"{o}（{n_by.get(o, 0)} 件）" for o in q1o], x=share, name=KIND_JA[k], orientation="h", marker_color=col,
                        text=[f"{v:.0%}" for v in share], textposition="inside", marker_line_color="rgba(0,0,0,0)")
        fig.update_layout(barmode="stack")
        fig.update_xaxes(tickformat=".0%", range=[0, 1], title="チェックインに占める割合")
        fig.update_yaxes(type="category", title=qlab("q1"), autorange="reversed")
        plot(style(fig, P, 300, "チェックインの内訳（q1 の回答別）", "括弧内はその回答の人たちのチェックイン件数の合計"))
        r_off = spearman_ci(ordinal(a.q1, orders["q1"]), a.off_card_share)
        finding([f"q1 と、本人のチェックインに占めるカード外の割合: {rho_words(r_off)}",
                 "負の関連なら「ビンゴを感じた人ほどカード上のブースを回った（直接の誘導）」、"
                 "正の関連なら「カードをきっかけに、カード外にも回遊が広がった（波及）」と読める。"])
        reading([("左の図だけ右肩上がり", "ビンゴの推薦によって訪問が増えた（主観と行動が一致）"),
                 ("左右とも右肩上がり", "もともとよく回る人が「変わった」と答えただけ。ビンゴの効果とは言えない"),
                 ("どちらも平ら", "実感は件数の増加としては表れていない（回る順番や選び方が変わった可能性）")],
                "回答者はチェックインが多めの層（② を参照）。差が出にくい方向に偏っている")

    # ---------------- ⑤ 推薦方式
    with tabs[4]:
        rq_intro("どの推薦方式を多く受けた人が、「行きたくなった」「予想外によかった」「予定外のブースに興味を惹かれた」と答えたか。",
                 "推薦方式（RANDOM / DRSA / SER / EXPLORE）は、同じ人の中でもマスごとに変わる。"
                 "1 人 1 回答のアンケートを方式と結びつけるには、その人が受けた推薦の中で各方式が占めた割合を使う必要がある。",
                 "1 人ごとに「受けた推薦枠のうち、その方式が何割だったか」を求め、q2・q3・q5 の回答との順位相関（ρ）を方式ごとに計算する。")
        rr = r[r.rec_slots > 0]
        SC = strategy_colors(P)
        mean_share = [rr[f"share_{s}"].mean() for s in STRATEGIES]
        fig = go.Figure()
        for s, v in zip(STRATEGIES, mean_share):
            fig.add_bar(y=["回答者の平均"], x=[v], name=STRATEGY_JA[s], orientation="h", marker_color=SC[s],
                        text=[f"{s} {v:.0%}"], textposition="inside", marker_line_color="rgba(0,0,0,0)")
        fig.update_layout(barmode="stack", showlegend=True)
        fig.update_xaxes(tickformat=".0%", range=[0, 1])
        fig.update_yaxes(type="category", showticklabels=False)
        plot(style(fig, P, 190, "回答者が受けた推薦の内訳", f"推薦を受けた回答者 {len(rr)} 人の、1 人ごとの割合の平均"))
        cols = [q for q in ("q2", "q3", "q5")]
        res = {(STRATEGY_JA[s], qlab(q)): spearman_ci(rr[f"share_{s}"], ordinal(rr[q], orders[q])) for s in STRATEGIES for q in cols}
        ff = style(forest_fig([STRATEGY_JA[s] for s in STRATEGIES], [qlab(q) for q in cols], res, P, [QSHORT[q] for q in cols]), P, 360,
                   "各方式の割合と、アンケートの回答の関連", "点 = 順位相関 ρ、横線 = 95% 信頼区間。青 = 0 を含まない（関連あり）、灰 = 判断できない")
        ff.update_layout(margin=dict(t=115))  # 列ごとの見出しの分だけ上を空ける
        plot(ff)
        sig = [f"{k[0]} × {k[1]}（{fmt_rho(v)}）" for k, v in res.items() if v[0] is not None and (v[1] > 0 or v[2] < 0)]
        finding(["信頼区間が 0 を含まない組み合わせ: " + ("; ".join(sig) if sig else "**なし**。この標本では、受けた推薦方式の違いと回答の関連は確認できない"),
                 "正の ρ = その方式を多く受けた人ほど肯定的に答えた。SER・EXPLORE は本人の関心外のブースを出す方式なので、"
                 "q3・q5（予想外・予定外）と正の関連があれば、関心外の推薦が体験として届いたことを示す。"])
        st.markdown("##### 参考: 推薦枠そのものの結果（参加者・出展者全員）")
        st.caption("アンケートとは別に、推薦枠 1 つを 1 件として、方式ごとに訪問された割合とセレンディピティの割合を比べる")
        ms = slots[slots.strategy.isin(STRATEGIES)]
        fig = go.Figure()
        for name, col, colr, dx in (("訪問された割合", "visited", P["s1"], -0.12), ("セレンディピティの割合", "serendipity", P["s2"], 0.12)):
            st_ = [wilson(int(ms.loc[ms.strategy == s, col].sum()), int((ms.strategy == s).sum())) for s in STRATEGIES]
            fig.add_trace(go.Scatter(
                x=[i + dx for i in range(len(STRATEGIES))], y=[p for p, *_ in st_], mode="markers+text", name=name,
                marker=dict(size=11, color=colr), text=[f"{p:.0%}" if p is not None else "" for p, *_ in st_],
                textposition="middle right", textfont=dict(color=P["ink"]),
                error_y=dict(type="data", symmetric=False, color=colr, thickness=2, width=5,
                             array=[(h - p) if p is not None else None for p, l, h in st_],
                             arrayminus=[(p - l) if p is not None else None for p, l, h in st_])))
        n_s = [int((ms.strategy == s).sum()) for s in STRATEGIES]
        fig.update_xaxes(tickvals=list(range(len(STRATEGIES))), ticktext=[f"{STRATEGY_JA[s]}<br>n={n}枠" for s, n in zip(STRATEGIES, n_s)])
        fig.update_yaxes(tickformat=".0%", rangemode="tozero", title="割合")
        plot(style(fig, P, 360, "方式ごとの、推薦先の訪問とセレンディピティ", "点 = 割合、ひげ = 95% 信頼区間（Wilson 法）"))
        reading([("フォレストプロットで青い点（0 を含まない）がある", "その方式とその体験に関連がある。方式の効果の候補として報告できる"),
                 ("すべて灰色", "標本（61 人）では方式による違いを検出できない。効果がないとは言えず、検出力不足の可能性"),
                 ("下の図で SER・EXPLORE のセレンディピティが高い", "関心外を出す方式が、行動上も「予想外によかった」を生んでいる")],
                "同じ人が複数の方式を受けており、割合は互いに独立ではない（1 つの割合が上がれば他が下がる）。因果ではなく関連として読む")

    # ---------------- ⑥ 主観と行動
    with tabs[5]:
        rq_intro("「行きたくなった」「予想外によかった」「興味を惹かれた」と答えた人は、行動でもそうしていたか。",
                 "主観と行動が一致すれば、アンケートを推薦の効果の指標として使える。一致しなければ、どちらかの定義を見直す必要がある。",
                 "q2 は推薦先の訪問率、q3・q5 はセレンディピティ（関心外の推薦先に行って★4）の件数と照合する。")
        pairs = [("q2", "rec_visit_rate", "推薦先の訪問率（1 人ごと）", "{:.0%}"),
                 ("q3", "serendipity", "セレンディピティの件数（1 人あたり）", "{:.2f}"),
                 ("q5", "serendipity", "セレンディピティの件数（1 人あたり）", "{:.2f}")]
        lines = []
        for q, var, ylab, f in pairs:
            o = substantive(orders[q])
            x = rr[rr[q].isin(o)]
            fig = dot_ci_fig(o, [mean_ci(x.loc[x[q] == a, var]) for a in o], P, qlab(q), ylab, fmt=f)
            if var == "rec_visit_rate":
                fig.update_yaxes(tickformat=".0%")
            plot(style(fig, P, 340, f"{qlab(q)} × {ylab}", "点 = 平均、ひげ = 95% 信頼区間"))
            lines.append(f"{qlab(q)} × {ylab}: {rho_words(spearman_ci(ordinal(x[q], orders[q]), x[var]))}")
        finding(lines)
        reading([("右肩上がりで ρ の信頼区間が 0 を含まない", "主観と行動が一致。アンケートは推薦の効果の指標として妥当"),
                 ("q3・q5 だけ一致しない", "本人の「予想外」は、推薦時の関心（事前アンケート）とは別の基準。セレンディピティの定義を見直す"),
                 ("q2 が一致しない", "行きたくなっても訪問には結びついていない（時間・混雑などの制約）")],
                "セレンディピティは件数が少なく 0 の人が多いため、平均が数人の値で動きやすい")

    # ---------------- データ表
    with tabs[6]:
        st.caption("回答者 1 人 1 行。user_id で SQL ページの各テーブルと結合できる。CSV は full/ と同じ扱い（個人の行動履歴）")
        cols = ["user_id", "role", *QUESTIONS, "qr_checkins", "q4_match", "q4_gap", "checkins", "checkins_before_first_rec",
                *[f"visits_{k}" for k in KIND_JA], "off_card_share", "rec_slots", "rec_visits", "rec_visit_rate",
                *[f"share_{s}" for s in STRATEGIES], "serendipity", "bingo_achieved", "gacha_coins", "ratings", "age_range"]
        st.dataframe(r[cols], hide_index=True, height=480)
        st.download_button("CSV をダウンロード", r[cols].to_csv(index=False).encode("utf-8-sig"),
                           file_name="research_respondents.csv", mime="text/csv")
        st.markdown("""
| 列 | 意味 |
|---|---|
| qr_checkins / q4_match / q4_gap | 記録上の QR チェックイン数 / 申告の帯と一致 / 帯の端からのずれ（負 = 少なく申告） |
| checkins_before_first_rec | 最初の推薦マスが開く前のチェックイン数（もともとの回り方） |
| visits_CENTER / OUTER / OFF | カードの真ん中のマス / 外側のマス / カードに無いブースへのチェックイン数 |
| rec_visit_rate | 受けた推薦枠のうち、訪問した割合 |
| share_* | 受けた推薦枠に占める各方式の割合 |
| serendipity | 推薦時点で関心外だったブースに行き、★4 を付けた件数 |
""")


# ---------------------------------------------------------------- 📌 知りたいことへの答え
# 研究者（ユーザー）がこのセッションで問うた疑問に、問いごとに 1 つの図と 1 文の答えで返す。
# 上部に「答えの一覧」を置き、各問いの詳細（図・数値・限界）はその下に続ける。

def _verdict(d: tuple, unit: str, fmt: str = "{:+.1f}") -> str:
    m, lo, hi = d
    if m is None:
        return "人数が足りず判断できない"
    word = "差がある" if lo > 0 or hi < 0 else "差があるとは言えない（信頼区間が 0 をまたぐ）"
    return f"差 {fmt.format(m)}{unit}［95%CI {fmt.format(lo)}, {fmt.format(hi)}］→ **{word}**"


def _sig(d: tuple) -> bool:
    return d[0] is not None and (d[1] > 0 or d[2] < 0)


def _two_group_fig(rows, var, P, ylab, pct=False, labels=("否定側（1・2）", "肯定側（3・4）")) -> go.Figure:
    """設問ごとに否定側（赤）と肯定側（青）の平均と 95% 信頼区間を並べる。rows = [(q, 否定側, 肯定側)]"""
    fig = go.Figure()
    for label, idx, color, dx in ((labels[0], 1, P["neg2"], -0.12), (labels[1], 2, P["pos2"], 0.12)):
        st_ = [mean_ci(g[idx][var]) for g in rows]
        f = "{:.0%}" if pct else "{:.1f}"
        fig.add_trace(go.Scatter(
            x=[i + dx for i in range(len(rows))], y=[m for m, *_ in st_], mode="markers+text", name=label,
            marker=dict(size=11, color=color), text=[f.format(m) if m is not None else "" for m, *_ in st_],
            textposition="middle right", textfont=dict(color=P["ink"]),
            error_y=dict(type="data", symmetric=False, color=color, thickness=2, width=5,
                         array=[(h - m) if h is not None else None for m, l, h, n in st_],
                         arrayminus=[(m - l) if l is not None else None for m, l, h, n in st_]),
            hovertext=[f"{QSHORT[q]} {label}: 平均 {f.format(m)}（n={n}）" if m is not None else "" for (q, *_), (m, l, h, n) in zip(rows, st_)],
            hoverinfo="text"))
    fig.update_xaxes(tickvals=list(range(len(rows))),
                     ticktext=[f"{QSHORT[q]}<br>n = {len(neg)} 対 {len(pos)}" for q, neg, pos in rows])
    fig.update_yaxes(rangemode="tozero", title=ylab, tickformat=".0%" if pct else None)
    return fig


def _split(x: pd.DataFrame, q: str, orders: dict):
    o = substantive(orders[q])
    return q, x[x[q].isin(o[:2])], x[x[q].isin(o[2:4])]


def answers_tab(u: pd.DataFrame, r: pd.DataFrame, visits: pd.DataFrame, slots: pd.DataFrame, orders: dict, P: dict) -> None:
    summary = st.container()  # 最後に「答えの一覧」を埋める
    ans: list[tuple[str, str]] = []
    nonresp = u[~u.responded]
    act_non = nonresp[nonresp.checkins > 0]
    x = r[r.checkins > 0].assign(card_share=lambda d: d.card_visits_after_assigned / d.checkins)

    # ---- 1
    st.divider()
    st.subheader("問い 1. 結局、訪問数が多い人がアンケートに答えているのか")
    st.caption("当日のチェックイン数を、回答者・アプリを使った非回答者（1 件以上）・非回答者全員で比べる。")
    c1, c2 = st.columns(2)
    with c1:
        fig = dot_ci_fig(["回答者", "非回答者<br>（アプリ利用）", "非回答者<br>（全員）"],
                         [mean_ci(r.checkins), mean_ci(act_non.checkins), mean_ci(nonresp.checkins)], P, "", "チェックイン数（平均）")
        plot(style(fig, P, 360, "グループごとのチェックイン数", "点 = 平均、ひげ = 95% 信頼区間"))
    with c2:
        b = u.assign(帯=u.checkins.map(checkin_bin)).groupby("帯").agg(対象=("user_id", "size"), 回答=("responded", "sum"))
        b = b.reindex([lab for *_, lab in CHECKIN_BINS]).fillna(0).astype(int)
        ci = [wilson(int(k), int(n)) for k, n in zip(b.回答, b.対象)]
        fig = dot_ci_fig(list(b.index), [(p, lo, hi, int(n)) for (p, lo, hi), n in zip(ci, b.対象)], P,
                         "当日のチェックイン数", "アンケートの回答率", fmt="{:.0%}")
        fig.update_yaxes(tickformat=".0%", range=[0, 1])
        plot(style(fig, P, 360, "チェックイン数の帯ごとの回答率", "右肩上がりなら、よく回った人ほど答えている"))
    d_all, d_act = diff_ci(r.checkins, nonresp.checkins), diff_ci(r.checkins, act_non.checkins)
    if _sig(d_act) and d_act[0] > 0:
        a1 = "はい。アプリを使った人どうしで比べても、回答者のほうが多く回っている。アンケートは「よく回った人」の声として読む。"
    elif _sig(d_all) and d_all[0] > 0:
        a1 = "見かけ上はそうだが、主な理由はアプリを使わなかった人が答えていないこと。アプリを使った人どうしでは差があるとは言えない。"
    else:
        a1 = "いいえ。回答者と非回答者の訪問数に差があるとは言えない。"
    finding([f"回答者 − 非回答者全員: {_verdict(d_all, ' 件')}", f"回答者 − アプリを使った非回答者: {_verdict(d_act, ' 件')}",
             f"**答え: {a1}**"])
    ans.append(("訪問数が多い人がアンケートに答えているか", a1))

    # ---- 2
    st.divider()
    st.subheader("問い 2. 「ビンゴの影響で訪問数が変わった」と答えた人は、実際に多く回ったのか")
    st.caption("q1 の肯定側（3・4）と否定側（1・2）で、当日のチェックイン数を比べる。比較のため、最初の推薦マスが開く前の"
               "チェックイン数（＝ビンゴの推薦が効く前の、もともとの回り方）も並べる。")
    q, neg, pos = _split(x, "q1", orders)
    rows = [("q1", neg, pos)]
    c1, c2 = st.columns(2)
    with c1:
        plot(style(_two_group_fig(rows, "checkins", P, "チェックイン数（平均）"), P, 340, "当日のチェックイン数", "点 = 平均、ひげ = 95% 信頼区間"))
    with c2:
        plot(style(_two_group_fig(rows, "checkins_before_first_rec", P, "チェックイン数（平均）"), P, 340,
                   "比較: 推薦が始まる前のチェックイン数", "ここでも差があれば、もともとよく回る人だっただけ"))
    d_day, d_pre = diff_ci(pos.checkins, neg.checkins), diff_ci(pos.checkins_before_first_rec, neg.checkins_before_first_rec)
    if _sig(d_day) and d_day[0] > 0 and not _sig(d_pre):
        a2 = "はい。「変わった」と答えた人は当日多く回っており、推薦が始まる前には差がない。ビンゴが始まってから差が開いた。"
    elif _sig(d_day) and d_day[0] > 0:
        a2 = "多く回っているが、推薦が始まる前から差がある。ビンゴの効果というより、もともとよく回る人が「変わった」と答えている。"
    else:
        a2 = "いいえ。「変わった」と答えた人とそうでない人で、実際の訪問数に差があるとは言えない。"
    finding([f"当日（肯定側 − 否定側）: {_verdict(d_day, ' 件')}", f"推薦前（肯定側 − 否定側）: {_verdict(d_pre, ' 件')}", f"**答え: {a2}**"])
    ans.append(("「訪問数が変わった」と答えた人は実際に多く回ったか", a2))

    # ---- 3
    st.divider()
    st.subheader("問い 3. 「訪問数が変わった」「意外な出会いがあった」と答えた人は、ビンゴカードのブースに行ったのか（ビンゴの影響と言えるか）")
    st.caption("本人のチェックインのうち「ビンゴカードに載った後に、そのブースへ行った」ものの割合を、肯定側と否定側で比べる。"
               "割合で比べるのは、よく回る人ほど件数が増えるのを除くため。")
    rows = [_split(x, q, orders) for q in ("q1", "q3", "q5") if len(substantive(orders[q])) >= 4]
    plot(style(_two_group_fig(rows, "card_share", P, "カードに載った後に訪問した割合", pct=True), P, 380,
               "チェックインのうち「ビンゴカードに載った後に訪問した」割合", "青（肯定側）が赤（否定側）より高ければ、実感とビンゴの訪問が一致"))
    c1, c2 = st.columns(2)
    with c1:
        plot(style(_two_group_fig(rows, "card_visits_after_assigned", P, "件数（平均）"), P, 330, "参考: カードに載った後の訪問（件数）"))
    with c2:
        plot(style(_two_group_fig(rows, "visits_OFF", P, "件数（平均）"), P, 330, "参考: カード外の訪問（件数）",
                   "肯定側がこちらも多いなら、単によく回る人"))
    lines, a3s = [], []
    for q, neg, pos in rows:
        d = diff_ci(pos.card_share, neg.card_share)
        dc, do = diff_ci(pos.card_visits_after_assigned, neg.card_visits_after_assigned), diff_ci(pos.visits_OFF, neg.visits_OFF)
        lines.append(f"{QSHORT[q]}（否定側 {len(neg)} 人・肯定側 {len(pos)} 人）: 割合の"
                     + _verdict(tuple(None if v is None else v * 100 for v in d), " ポイント"))
        if _sig(d) and d[0] > 0:
            a3s.append(f"{QSHORT[q]}: 肯定した人ほどカードのブースへ行っている（ビンゴの影響を支持）")
        elif _sig(dc) and dc[0] > 0 and _sig(do) and do[0] > 0:
            a3s.append(f"{QSHORT[q]}: カードもカード外も多く回っただけ（ビンゴの影響とは言えない）")
        else:
            a3s.append(f"{QSHORT[q]}: カードのブースへの行き方に差はない（ビンゴの影響とは言えない）")
    finding(lines + [f"**答え: {a}**" for a in a3s])
    ans.append(("「増えた」「意外な出会い」と答えた人はビンゴカードのブースに行ったか", " / ".join(a3s)))
    st.caption("限界: 「カードに載った後の訪問」には、ビンゴがなくても行ったかもしれない訪問を含む。ここで言えるのは実感と行動の一致（関連）まで。")

    # ---- 4
    st.divider()
    st.subheader("問い 4. 「開いたマスに行きたくなった」「予想外によかった」「予定外に興味を惹かれた」に当てはまる推薦方式は何か")
    st.caption("推薦方式は同じ人の中でもマスごとに変わるので、1 人ごとに「受けた推薦のうち各方式が何割だったか」を求め、"
               "肯定側と否定側でその内訳を比べる。")
    rr = r[r.rec_slots > 0]
    rows = [_split(rr, q, orders) for q in ("q2", "q3", "q5") if len(substantive(orders[q])) >= 4]
    SC = strategy_colors(P)
    fig = go.Figure()
    ylabels = [f"{QSHORT[q]}・{side}（{len(g)} 人）" for q, neg, pos in rows for side, g in (("否定側", neg), ("肯定側", pos))]
    for s_ in STRATEGIES:
        vals = [g[f"share_{s_}"].mean() if len(g) else 0 for q, neg, pos in rows for g in (neg, pos)]
        fig.add_bar(y=ylabels, x=vals, name=STRATEGY_JA[s_], orientation="h", marker_color=SC[s_],
                    text=[f"{v:.0%}" for v in vals], textposition="inside", marker_line_color="rgba(0,0,0,0)")
    fig.update_layout(barmode="stack")
    fig.update_xaxes(tickformat=".0%", range=[0, 1], title="受けた推薦に占める割合（1 人ごとの平均）")
    fig.update_yaxes(type="category", autorange="reversed")
    plot(style(fig, P, 120 + 45 * len(ylabels), "肯定側と否定側が受けた推薦の内訳",
               "同じ設問の 2 本で、ある方式の帯が肯定側だけ長ければ、その方式がその体験に結びついている"))
    lines, hits = [], []
    for q, neg, pos in rows:
        for s_ in STRATEGIES:
            d = diff_ci(pos[f"share_{s_}"], neg[f"share_{s_}"])
            if d[0] is None:
                continue
            if _sig(d):
                hits.append(f"{QSHORT[q]} × {s_}（肯定側のほうが {d[0] * 100:+.1f} ポイント）")
        best = max(STRATEGIES, key=lambda s_: (pos[f"share_{s_}"].mean() - neg[f"share_{s_}"].mean()) if len(pos) and len(neg) else -9)
        dbest = diff_ci(pos[f"share_{best}"], neg[f"share_{best}"])
        lines.append(f"{QSHORT[q]}: 肯定側で最も多かった方式は {best}（" + _verdict(tuple(None if v is None else v * 100 for v in dbest), " ポイント") + "）")
    a4 = ("信頼区間が 0 をまたがない組み合わせ: " + "、".join(hits)) if hits else \
        "この標本では、どの体験にも特定の推薦方式との関連は確認できない（方式間の差は誤差の範囲）。"
    finding(lines + [f"**答え: {a4}**"])
    ans.append(("「行きたくなった」「予想外によかった」に当てはまる推薦方式", a4))
    st.caption("限界: 1 人が複数の方式を受けており、割合は互いに独立ではない。方式ごとの体験を直接聞いたわけではないため、関連として読む。")

    # ---- 5
    st.divider()
    st.subheader("問い 5. 推薦方式は、1 人あたりどんな割合で使われたのか")
    st.caption("回答者 1 人ごとの、受けた推薦（RANDOM / DRSA / SER / EXPLORE）の内訳。1 本の棒が 1 人。")
    order_ = rr.sort_values([f"share_{s_}" for s_ in STRATEGIES], ascending=False)
    fig = go.Figure()
    for s_ in STRATEGIES:
        fig.add_bar(x=list(range(1, len(order_) + 1)), y=order_[f"share_{s_}"], name=STRATEGY_JA[s_], marker_color=SC[s_],
                    hovertext=[f"{s_} {v:.0%}（推薦 {int(n)} 枠）" for v, n in zip(order_[f"share_{s_}"], order_.rec_slots)],
                    hoverinfo="text", marker_line_width=0)
    fig.update_layout(barmode="stack", bargap=0.1)
    fig.update_xaxes(title="回答者（RANDOM の割合が多い順）", showticklabels=False)
    fig.update_yaxes(tickformat=".0%", range=[0, 1], title="受けた推薦に占める割合")
    plot(style(fig, P, 360, "回答者ごとの推薦方式の内訳", f"推薦を受けた回答者 {len(rr)} 人"))
    mix = pd.DataFrame({"方式": [STRATEGY_JA[s_] for s_ in STRATEGIES],
                        "平均の割合": [rr[f"share_{s_}"].mean() for s_ in STRATEGIES],
                        "受けた人": [int((rr[f"slots_{s_}"] > 0).sum()) for s_ in STRATEGIES],
                        "受けた枠の合計": [int(rr[f"slots_{s_}"].sum()) for s_ in STRATEGIES]})
    st.dataframe(mix.style.format({"平均の割合": "{:.1%}"}), hide_index=True)
    a5 = "、".join(f"{s_} {v:.0%}" for s_, v in zip(STRATEGIES, mix.平均の割合)) + f"（推薦 1 人あたり平均 {rr.rec_slots.mean():.1f} 枠）"
    ans.append(("1 人あたりの推薦方式の割合（回答者の平均）", a5))

    # ---- 6
    st.divider()
    st.subheader("問い 6. 自己申告の QR チェックイン数（q4）は、実際と合っているか")
    a2_ = r[r.q4_lo.notna()]
    if len(a2_):
        m1, m2 = wilson(int(a2_.q4_match.sum()), len(a2_)), wilson(int((a2_.q4_band_dist <= 1).sum()), len(a2_))
        band_order = [b_ for b_ in orders["q4"] if q4_band(b_)]
        ct = pd.crosstab(a2_.q4, a2_.qr_band).reindex(index=band_order, columns=band_order).fillna(0).astype(int)
        fig = go.Figure(go.Heatmap(z=ct.values, x=ct.columns, y=ct.index, colorscale=[[0, "rgba(0,0,0,0)"], [1, P["s1"]]],
                                   text=ct.values, texttemplate="%{text}", showscale=False,
                                   hovertemplate="申告 %{y} × 記録 %{x}: %{z} 人<extra></extra>"))
        fig.update_xaxes(type="category", title="記録上の QR チェックイン数")
        fig.update_yaxes(type="category", title="本人の申告（q4）", autorange="reversed")
        plot(style(fig, P, 360, "本人の申告 × 記録", "対角線上が一致"))
        a6 = f"帯が一致 {m1[0]:.0%}（95%CI {m1[1]:.0%}–{m1[2]:.0%}）、隣の帯まで含めると {m2[0]:.0%}（n = {len(a2_)}）"
        finding([f"**答え: {a6}**", f"少なく申告 {(a2_.q4_gap < 0).mean():.0%}・多く申告 {(a2_.q4_gap > 0).mean():.0%}・"
                                    f"覚えていない {int(r.q4_lo.isna().sum())} 人"])
        ans.append(("自己申告の QR チェックイン数は正確か", a6))

    # ---- 7
    st.divider()
    st.subheader("問い 7. チェックインは QR とコード入力がそれぞれ何割だったか")
    st.caption("当日の全チェックイン（参加者 + 出展者、アンケート回答の有無を問わない）。")
    m = visits.checkin_method.fillna("不明").value_counts()
    names = {"qr": "QR 読み取り", "manual": "コード入力"}
    tot = int(m.sum())
    fig = go.Figure()
    for (k, v), col in zip(m.items(), [P["s1"], P["s2"], P["neutral"]]):
        fig.add_bar(y=["チェックイン"], x=[v / tot], name=names.get(k, k), orientation="h", marker_color=col,
                    text=[f"{names.get(k, k)} {v / tot:.0%}（{v} 件）"], textposition="inside", marker_line_color="rgba(0,0,0,0)")
    fig.update_layout(barmode="stack")
    fig.update_xaxes(tickformat=".0%", range=[0, 1])
    fig.update_yaxes(type="category", showticklabels=False)
    plot(style(fig, P, 180, "チェックイン手法の内訳", f"当日のチェックイン {tot} 件"))
    users_by = visits.groupby("checkin_method").user_id.nunique()
    a7 = "、".join(f"{names.get(k, k)} {v / tot:.0%}（{v} 件）" for k, v in m.items())
    finding([f"**答え: {a7}**", "使った人数: " + "、".join(f"{names.get(k, k)} {n} 人" for k, n in users_by.items())
             + "（両方使った人は両方に数える）"])
    ans.append(("チェックイン手法（QR / コード入力）の割合", a7))

    satisfaction_section(slots, visits, P, ans)

    with summary:
        st.markdown("### 答えの一覧")
        st.caption("このセッションで問われた疑問への、データからの答え。根拠の図と数値は、下に問いの順で続く。95% 信頼区間が 0 をまたぐ差は「差があるとは言えない」とした。")
        with st.container(border=True):
            st.markdown("\n".join(f"{i}. **{q}** — {a}" for i, (q, a) in enumerate(ans, 1)))


# ---------------------------------------------------------------- 問い 8. 推薦方式ごとの満足度

def satisfaction_section(slots: pd.DataFrame, visits: pd.DataFrame, P: dict, ans: list) -> None:
    """推薦先に行って付けた★を方式ごとに比べる。対象はアンケート回答者に限らない（参加者 + 出展者全員の行動）。
    SQL で同じ集計を確かめるには explorer/queries/rating_by_strategy.sql。"""
    st.divider()
    st.subheader("問い 8. 推薦方式ごとの満足度（高評価・低評価・ばらつき）は違うか")
    st.caption("推薦先に行ったときに付けた★（1〜4）を方式ごとに比べる。比較の基準として、推薦枠に当たらない訪問（自分で選んだブース）の★も並べる。"
               "対象は参加者 + 出展者全員（アンケート回答の有無を問わない）。同じ集計の SQL: 保存したクエリ「rating_by_strategy」")
    SELF = "推薦外の訪問"
    rec = slots[slots.strategy.isin(STRATEGIES) & slots.visited].assign(grp=lambda d: d.strategy)
    own = visits[~visits.id.isin(slots.visit_checkin_id.dropna())].assign(grp=SELF, interest_match=None)
    allv = pd.concat([rec[["grp", "interest_match", "rating"]], own[["grp", "interest_match", "rating"]]], ignore_index=True)
    groups = STRATEGIES + [SELF]
    rated = allv.dropna(subset=["rating"])
    rated = rated[rated.rating.between(1, 4)]
    n_v = allv.groupby("grp").size().reindex(groups).fillna(0).astype(int)
    n_r = rated.groupby("grp").size().reindex(groups).fillna(0).astype(int)

    # 8-1 分布
    colors = {1: P["neg2"], 2: P["neg1"], 3: P["pos1"], 4: P["pos2"]}
    fig = go.Figure()
    ylab = [f"{STRATEGY_JA.get(g, g)}（評価 {n_r[g]} / 訪問 {n_v[g]}）" for g in groups]
    for k in (1, 2, 3, 4):
        share = [(rated[rated.grp == g].rating == k).mean() if n_r[g] else 0 for g in groups]
        fig.add_bar(y=ylab, x=share, name=f"★{k}", orientation="h", marker_color=colors[k], legendrank=k,
                    text=[f"{v:.0%}" if v >= 0.05 else "" for v in share], textposition="inside",
                    textfont=dict(color="#fff" if k in (1, 4) else P["ink"]), marker_line_color="rgba(0,0,0,0)",
                    hovertext=[f"★{k}: {v:.1%}（{int(round(v * n))} 件）" for v, n in zip(share, n_r)], hoverinfo="text")
    fig.update_layout(barmode="stack")
    fig.update_xaxes(tickformat=".0%", range=[0, 1], title="評価に占める割合")
    fig.update_yaxes(type="category", autorange="reversed")
    plot(style(fig, P, 330, "方式ごとの★の分布", "評価が付いた訪問だけ。右（青）が長いほど高評価が多い"))
    st.caption("評価率（評価を付けた訪問 ÷ 訪問）: " + "、".join(f"{g} {n_r[g] / n_v[g]:.0%}" for g in groups if n_v[g])
               + "。評価率が低い方式の★は、評価を付けた一部の人の声である点に注意。")

    # 8-2 平均・ばらつき・★4（高評価）・★1–2（低評価）の割合
    from plotly.subplots import make_subplots
    fig = make_subplots(rows=1, cols=4, shared_yaxes=True, horizontal_spacing=0.04,
                        subplot_titles=["平均の★", "ばらつき（標準偏差）", "★4（高評価）の割合", "★1・2（低評価）の割合"])
    labs = [STRATEGY_JA.get(g, g) for g in groups]
    stats = [
        [mean_ci(rated.loc[rated.grp == g, "rating"]) for g in groups],
        [sd_ci(rated.loc[rated.grp == g, "rating"]) for g in groups],
        [(*wilson(int((rated.loc[rated.grp == g, "rating"] == 4).sum()), int(n_r[g])), int(n_r[g])) for g in groups],
        [(*wilson(int((rated.loc[rated.grp == g, "rating"] <= 2).sum()), int(n_r[g])), int(n_r[g])) for g in groups],
    ]
    fmts = ["{:.2f}", "{:.2f}", "{:.0%}", "{:.0%}"]
    for j, (st_, f) in enumerate(zip(stats, fmts), 1):
        cols_ = [P["muted"] if g == SELF else (P["neg2"] if j == 4 else P["s1"]) for g in groups]
        for lab, (m, lo, hi, n), col in zip(labs, st_, cols_):
            if m is None:
                continue
            fig.add_trace(go.Scatter(x=[lo, hi], y=[lab, lab], mode="lines", line=dict(color=col, width=2),
                                     hoverinfo="skip", showlegend=False), 1, j)
            fig.add_trace(go.Scatter(x=[m], y=[lab], mode="markers+text", marker=dict(size=10, color=col),
                                     text=[f.format(m)], textposition="top center", textfont=dict(color=P["ink"], size=11),
                                     hovertext=[f"{lab}: {f.format(m)}（95%CI {f.format(lo)}–{f.format(hi)}, n={n}）"],
                                     hoverinfo="text", showlegend=False), 1, j)
    fig.update_xaxes(tickformat=".0%", col=3, range=[0, 1])
    fig.update_xaxes(tickformat=".0%", col=4, range=[0, 1])
    fig.update_yaxes(type="category", autorange="reversed")
    for a in fig.layout.annotations:
        a.font = dict(size=12, color=P["ink"])
    ff = style(fig, P, 360, "方式ごとの平均・ばらつき・高評価と低評価の割合",
               "点 = 推定値、横線 = 95% 信頼区間。灰色 = 推薦外の訪問（基準）。低評価の割合が高いほど「外れ」の推薦が多い")
    ff.update_layout(margin=dict(t=110))
    plot(ff)

    # 8-3 関心の一致度 × 方式
    st.markdown("##### 推薦時点の関心との一致度別の、高評価（★4）と低評価（★1・2）の割合")
    st.caption("推薦した時点で、ブースのカテゴリを本人の事前アンケートと照らした結果（推薦エンジンが記録した値）。"
               "MATCH = いちばん興味がある分野（第 1 希望）、PARTIAL = 興味があると選んだ分野だが第 1 希望ではない、"
               "MISMATCH = 興味がある分野として選ばなかった分野（関心外）。事前アンケート未回答などで判定できない UNKNOWN は図から除いた。"
               "SER・EXPLORE は関心外のブースを出す方式なので、MISMATCH での ★4 の割合が「関心外でも意外に満足した」かを表す。")
    IM = ["MATCH", "PARTIAL", "MISMATCH"]
    imc = {"MATCH": P["s1"], "PARTIAL": P["s3"], "MISMATCH": P["s2"]}
    rr_ = rated[rated.grp.isin(STRATEGIES)]
    c1, c2 = st.columns(2)
    for col_, title, hit in ((c1, "★4（高評価）の割合", lambda x: x == 4), (c2, "★1・2（低評価）の割合", lambda x: x <= 2)):
        fig = go.Figure()
        for im, dx in zip(IM, (-0.2, 0, 0.2)):
            st_ = []
            for g in STRATEGIES:
                x = rr_[(rr_.grp == g) & (rr_.interest_match == im)].rating
                st_.append((*wilson(int(hit(x).sum()), len(x)), len(x)))
            fig.add_trace(go.Scatter(
                x=[i + dx for i in range(len(STRATEGIES))], y=[m for m, *_ in st_], mode="markers", name=im,
                marker=dict(size=10, color=imc[im]),
                error_y=dict(type="data", symmetric=False, color=imc[im], thickness=2, width=4,
                             array=[(h - m) if m is not None else None for m, l, h, n in st_],
                             arrayminus=[(m - l) if m is not None else None for m, l, h, n in st_]),
                hovertext=[f"{g} × {im}: {m:.0%}（n={n}）" if m is not None else f"{g} × {im}: 評価なし"
                           for g, (m, l, h, n) in zip(STRATEGIES, st_)], hoverinfo="text"))
        fig.update_xaxes(tickvals=list(range(len(STRATEGIES))), ticktext=STRATEGIES)
        fig.update_yaxes(tickformat=".0%", range=[0, 1], title="割合")
        with col_:
            plot(style(fig, P, 360, f"方式 × 関心の一致度 の {title}", "点 = 割合、ひげ = 95% 信頼区間。n はカーソルで表示"))

    # 仮説ごとの判定
    R = lambda g: rated.loc[rated.grp == g, "rating"]
    hi4 = lambda d: (d == 4).astype(float)
    lo12 = lambda d: (d <= 2).astype(float)
    pt = lambda d: tuple(None if v is None else v * 100 for v in d)
    lines = []
    for g in ("RANDOM", "EXPLORE"):
        d = sd_diff_ci(R(g), R("DRSA"))
        lines.append(f"{g} のばらつきは DRSA より大きいか: 標準偏差の" + _verdict(d, "", "{:+.2f}"))
    d_drsa = diff_ci(hi4(R("DRSA")), hi4(R("RANDOM")))
    lines.append("DRSA は RANDOM より ★4 が多いか: ★4 の割合の" + _verdict(tuple(None if v is None else v * 100 for v in d_drsa), " ポイント"))
    low = {g: diff_ci(lo12(R(g)), lo12(R("DRSA"))) for g in ("RANDOM", "SER", "EXPLORE")}
    for g, d in low.items():
        lines.append(f"{g} は DRSA より ★1・2（低評価）が多いか: 低評価の割合の" + _verdict(pt(d), " ポイント"))
    ser_mis = rated[(rated.grp == "SER") & (rated.interest_match == "MISMATCH")].rating
    oth_mis = rated[(rated.grp.isin(["RANDOM", "DRSA", "EXPLORE"])) & (rated.interest_match == "MISMATCH")].rating
    d_ser = diff_ci(hi4(ser_mis), hi4(oth_mis))
    d_ser_self = diff_ci(hi4(ser_mis), hi4(R(SELF)))
    lines.append(f"SER の関心外（MISMATCH, n={len(ser_mis)}）は、他の方式の関心外より ★4 が多いか: "
                 + _verdict(tuple(None if v is None else v * 100 for v in d_ser), " ポイント"))
    d_ser_low = diff_ci(lo12(ser_mis), lo12(oth_mis))
    lines.append("SER の関心外の ★1・2（低評価）の割合（他の方式の関心外との差。正 = SER のほうが多い）: " + _verdict(pt(d_ser_low), " ポイント"))
    lines.append("SER の関心外は、自分で選んだ訪問と比べて ★4 が少ないか（関心外でも満足度が落ちていないか）: "
                 + _verdict(tuple(None if v is None else v * 100 for v in d_ser_self), " ポイント"))
    parts = []
    parts.append("RANDOM・EXPLORE のばらつきが DRSA より大きいとは"
                 + ("言える" if any(_sig(sd_diff_ci(R(g), R("DRSA"))) and sd_diff_ci(R(g), R("DRSA"))[0] > 0 for g in ("RANDOM", "EXPLORE")) else "言えない"))
    parts.append("DRSA の高評価が RANDOM より多いとは" + ("言える" if _sig(d_drsa) and d_drsa[0] > 0 else "言えない"))
    more_low = [g for g, d in low.items() if _sig(d) and d[0] > 0]
    fewer_low = [g for g, d in low.items() if _sig(d) and d[0] < 0]
    parts.append(("DRSA より低評価が多い方式: " + "・".join(more_low)) if more_low else "DRSA より低評価が多い方式は確認できない")
    if fewer_low:
        parts.append("DRSA より低評価が少ない方式: " + "・".join(fewer_low))
    parts.append("SER の関心外ブースが他方式の関心外より高評価とは" + ("言える" if _sig(d_ser) and d_ser[0] > 0 else "言えない")
                 + ("（自分で選んだ訪問と比べて満足度は落ちていない）" if not (_sig(d_ser_self) and d_ser_self[0] < 0) else "（自分で選んだ訪問より低い）"))
    if _sig(d_ser_low):
        parts.append("SER の関心外ブースは、他の方式の関心外より低評価が" + ("多い" if d_ser_low[0] > 0 else "少ない"))
    a8 = "。".join(parts) + "。"
    finding(lines + [f"**答え: {a8}**"])
    ans.append(("推薦方式ごとの満足度（★の高低とばらつき）", a8))
    st.caption("限界: 同じ人が複数の方式の推薦先を評価しており、評価は人ごとの付け方の癖（甘い・辛い）を含む。"
               "★は評価を付けた訪問だけなので、行ったが評価しなかった訪問の満足度はわからない。")
