"""当日の監視画面の指標。**算出式はここにだけ書く**（AGENTS.md「絶対に守ること」4）。

仕様: docs/specs/recommendation-evaluation/03-live-dashboard.md

原則:
- 各指標は「こうなったらこうする」（対応行動）とセット。行動の無い数字は載せない
- データ源は **DB**。`/ops/state` が取れなくても他は動かし続ける（02 §1）
- **A/B の効果（DRSA 枠と COVERAGE 枠の訪問率の差）をこのモジュールに書かない**（03 §5）。
  `experiment_progress()` は分母（提示数）しか返さない。訪問率の群別集計を実装しない。

すべて DataFrame を受け取り、素の dict / DataFrame を返す純関数。Streamlit に依存しない。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

import rec_db  # /ops/state の取得結果（OPS_AUTH_ERROR ほか）の区別を借りるだけ

GREEN, YELLOW, RED, UNKNOWN = "green", "yellow", "red", "unknown"

FALLBACK_STRATEGY = "FALLBACK_COVERAGE"
RECOMMEND_STRATEGY = "RECOMMEND"


@dataclass(frozen=True)
class Signal:
    """信号機の1項目。`action` は画面にそのまま印字する（03「対応行動を画面に印字する」）。"""

    key: str
    label: str
    value: float | int | str | None
    level: str
    detail: str
    action: str


def _level(value: float | None, yellow: float, red: float, *, higher_is_worse: bool) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return UNKNOWN
    if higher_is_worse:
        if value >= red:
            return RED
        if value >= yellow:
            return YELLOW
        return GREEN
    if value <= red:
        return RED
    if value <= yellow:
        return YELLOW
    return GREEN


def _to_utc(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _window(df: pd.DataFrame, ts_col: str, now: pd.Timestamp, minutes: int) -> pd.DataFrame:
    if df.empty:
        return df
    ts = pd.to_datetime(df[ts_col], utc=True)
    now_utc = _to_utc(now)
    start = now_utc - pd.Timedelta(minutes=minutes)
    return df[(ts > start) & (ts <= now_utc)]


# --- 信号機の各項目（03 §1）--------------------------------------------------


def fallback_rate(unlock_events: pd.DataFrame, now: pd.Timestamp, window_min: int = 30) -> Signal:
    """直近 window 分の解放のうち `strategy='FALLBACK_COVERAGE'` の割合。

    🟡10% / 🔴30%。最悪シナリオ（推薦エンジンが朝から死んでいるのに誰も気づかない）
    の検知がこの画面の最大の存在意義（03 §0）。
    """
    win = _window(unlock_events, "created_at", now, window_min)
    n = len(win)
    if n == 0:
        return Signal(
            "fallback_rate", f"フォールバック率（直近{window_min}分）", None, UNKNOWN,
            "直近の解放が無い", "解放数（下の項目）を確認する。極端に少なければサーバー側を見る",
        )
    rate = float((win["strategy"] == FALLBACK_STRATEGY).mean())
    return Signal(
        "fallback_rate", f"フォールバック率（直近{window_min}分）", rate,
        _level(rate, 0.10, 0.30, higher_is_worse=True),
        f"{n}件中 {int((win['strategy'] == FALLBACK_STRATEGY).sum())}件がフォールバック",
        "最優先。推薦エンジンのログを見る・再起動する・再デプロイする",
    )


def rating_recovery_rate(booth_ratings: pd.DataFrame, check_ins: pd.DataFrame) -> Signal:
    """`booth_ratings` 件数 ÷ `check_ins` 件数。🟡30% / 🔴25%（低いほど悪い）。"""
    n_check = len(check_ins)
    rate = float(len(booth_ratings) / n_check) if n_check else None
    return Signal(
        "rating_recovery_rate", "評価回収率", rate,
        _level(rate, 0.30, 0.25, higher_is_worse=False),
        f"評価 {len(booth_ratings)} / チェックイン {n_check}",
        "運営に声かけを依頼する（当日打てる数少ない手のひとつ）",
    )


def current_phase(unlock_events: pd.DataFrame, now: pd.Timestamp | None = None) -> Signal:
    """最新の `card_unlock_events.phase` と `decision_table_size`。

    15時を過ぎても `COVERAGE` なら 🔴（決定表が育っていない）。
    """
    if unlock_events.empty:
        return Signal("current_phase", "現在フェーズ", None, UNKNOWN, "解放イベントがまだ無い", "開場直後なら正常")
    latest = unlock_events.sort_values("created_at").iloc[-1]
    phase = str(latest["phase"])
    size = int(latest["decision_table_size"]) if pd.notna(latest["decision_table_size"]) else None
    ts = pd.to_datetime(latest["created_at"], utc=True)
    ref = ts if now is None else _to_utc(now)
    jst_hour = ref.tz_convert("Asia/Tokyo").hour
    level = RED if (phase == "COVERAGE" and jst_hour >= 15) else GREEN
    return Signal(
        "current_phase", "現在フェーズ / 決定表件数", phase, level,
        f"phase={phase} / decision_table_size={size}",
        "15時でも COVERAGE のままなら、評価回収率と併せて原因を追う",
    )


def recent_unlock_count(unlock_events: pd.DataFrame, now: pd.Timestamp, window_min: int = 30,
                        expected_min: int = 30) -> Signal:
    """直近 window 分の解放件数。想定より極端に少なければ解放処理側の問題。"""
    n = len(_window(unlock_events, "created_at", now, window_min))
    level = RED if n < expected_min else GREEN
    return Signal(
        "recent_unlock_count", f"直近{window_min}分の解放数", n, level,
        f"{n}件（目安 {expected_min}件以上）",
        "極端に少なければ解放処理側の問題。サーバー側を見る",
    )


def ops_state_signals(ops_state: dict | None, status: str | None = None) -> list[Signal]:
    """`/ops/state` 由来の項目（γ・規則本数・被覆率・応答時間）。

    `ops_state` が None のとき、各項目を「取得不能」（level=unknown）で返し、
    **他の指標は止めない**（03「/ops/state が取れないとき」）。

    `status` が `rec_db.OPS_AUTH_ERROR`（401/403）のときだけ「認証エラー」と出し分ける。
    **当日「トークンの設定漏れ」と「推薦エンジンが落ちている」を画面上で区別するため**であり、
    表示の親切さの話ではない。打てる手がまったく違う（前者は env を直す、後者はエンジンを見る）。
    """
    if ops_state is None:
        auth = status == rec_db.OPS_AUTH_ERROR
        na = "認証エラー" if auth else "取得不能"
        detail = (f"{na}（{rec_db.OpsStateClient.TOKEN_ENV} を確認）" if auth else na)
        first_action = (
            f"{rec_db.OpsStateClient.TOKEN_ENV} が未設定か誤っている。"
            "推薦サービスの OPS_TOKEN と同じ値を設定する（エンジン自体は生きている可能性が高い）"
            if auth else
            "/ops/state 取得失敗そのものが、フォールバック率と併せて障害のサイン。品質ゲートは下げない"
        )
        rest_action = (
            "認証を直せば取得できる" if auth else "取得でき次第"
        )
        return [
            Signal("drsa_quality", "DRSA 品質（γ・確実規則）", na, UNKNOWN, detail, first_action),
            Signal("rule_coverage", "規則の被覆率", na, UNKNOWN, detail,
                   f"{rest_action}、0.3 を下回っていないか確認する"),
            Signal("latency_p95", "応答時間 p95", na, UNKNOWN, detail,
                   f"{rest_action}、600ms 未満か確認する"),
        ]
    gamma = ops_state.get("gamma")
    n_certain = ops_state.get("n_certain_rules")
    coverage = ops_state.get("rule_coverage")
    p95 = ops_state.get("latency_p95_ms")

    gamma_level = _level(gamma, 0.5, 0.0, higher_is_worse=False)
    if n_certain is not None and n_certain <= 1:
        gamma_level = RED
    return [
        Signal("drsa_quality", "DRSA 品質（γ・確実規則）", gamma, gamma_level,
               f"γ={gamma} / 確実規則 {n_certain}本",
               "品質ゲートが正しく止めているか確認する。しきい値を下げない"),
        Signal("rule_coverage", "規則の被覆率", coverage, _level(coverage, 0.5, 0.3, higher_is_worse=False),
               f"被覆率 {coverage}", "大半の候補が判断保留（score=0.5）になっている可能性。規則生成側を見る"),
        Signal("latency_p95", "応答時間 p95", p95, _level(p95, 400, 600, higher_is_worse=True),
               f"p95 {p95}ms", "タイムアウト（1000ms）が近い。推薦エンジンの負荷を見る"),
    ]


def signal_board(unlock_events: pd.DataFrame, check_ins: pd.DataFrame, booth_ratings: pd.DataFrame,
                 ops_state: dict | None, now: pd.Timestamp,
                 ops_status: str | None = None) -> list[Signal]:
    """画面1（信号機）の全項目。この順で縦に並べる。"""
    return [
        fallback_rate(unlock_events, now),
        rating_recovery_rate(booth_ratings, check_ins),
        current_phase(unlock_events, now),
        recent_unlock_count(unlock_events, now),
        *ops_state_signals(ops_state, ops_status),
    ]


def worst_level(signals: list[Signal]) -> str:
    order = {GREEN: 0, UNKNOWN: 1, YELLOW: 2, RED: 3}
    return max((s.level for s in signals), key=lambda lv: order[lv], default=GREEN)


# --- 画面2 当日の時系列（03 §2）— 1枚のグラフに重ねる ----------------------


def time_series(check_ins: pd.DataFrame, booth_ratings: pd.DataFrame, unlock_events: pd.DataFrame,
                bin_min: int = 10) -> pd.DataFrame:
    """10分刻みの累計チェックイン・累計評価・決定表件数。1つの DataFrame に束ねて返す。"""
    def cumcount(df: pd.DataFrame, ts_col: str, name: str) -> pd.Series:
        if df.empty:
            return pd.Series(dtype="int64", name=name)
        t = pd.to_datetime(df[ts_col], utc=True).dt.floor(f"{bin_min}min")
        return t.value_counts().sort_index().cumsum().rename(name)

    c = cumcount(check_ins, "checked_in_at", "cum_checkins")
    r = cumcount(booth_ratings, "rated_at", "cum_ratings")

    if unlock_events.empty:
        d = pd.Series(dtype="float64", name="decision_table_size")
    else:
        u = unlock_events.copy()
        u["bin"] = pd.to_datetime(u["created_at"], utc=True).dt.floor(f"{bin_min}min")
        d = u.groupby("bin")["decision_table_size"].max().rename("decision_table_size")

    idx = c.index.union(r.index).union(d.index)
    out = pd.DataFrame(index=idx).join([c, r, d])
    out[["cum_checkins", "cum_ratings"]] = out[["cum_checkins", "cum_ratings"]].ffill().fillna(0)
    out["decision_table_size"] = out["decision_table_size"].ffill()
    return out.rename_axis("bin").reset_index()


def phase_change_times(unlock_events: pd.DataFrame) -> pd.DataFrame:
    """フェーズが切り替わった時刻（縦線用）。"""
    if unlock_events.empty:
        return pd.DataFrame(columns=["created_at", "phase"])
    u = unlock_events.sort_values("created_at")
    changed = u["phase"].ne(u["phase"].shift())
    return u.loc[changed, ["created_at", "phase"]].reset_index(drop=True)


# --- 画面3 異常検知（03 §3）------------------------------------------------


def assignment_concentration(recommendation_scores: pd.DataFrame) -> dict:
    """`was_assigned=1` のブース別件数の集中度。人気順への退化の実地検出（このアプリの失敗の定義）。"""
    assigned = recommendation_scores[recommendation_scores["was_assigned"] == 1]
    counts = assigned.groupby("booth_id").size().rename("n_assigned").sort_values(ascending=False)
    total = int(counts.sum())
    if total == 0:
        return {"n_assigned": 0, "top1_share": None, "gini": None, "top_booths": []}
    shares = (counts / total).to_numpy()
    return {
        "n_assigned": total,
        "top1_share": float(shares[0]),
        "gini": _gini(counts.to_numpy()),
        "top_booths": counts.head(5).reset_index().to_dict(orient="records"),
    }


def _gini(x: np.ndarray) -> float:
    if x.size == 0 or x.sum() == 0:
        return float("nan")
    s = np.sort(x.astype(float))
    n = s.size
    cum = np.cumsum(s)
    return float((n + 1 - 2 * (cum / cum[-1]).sum()) / n)


def empty_cells(bingo_cells: pd.DataFrame) -> int:
    """`is_revealed=1 AND booth_id IS NULL`。候補切れ（E7）。"""
    return int(((bingo_cells["is_revealed"] == 1) & (bingo_cells["booth_id"].isna())).sum())


def off_card_visit_rate(check_ins: pd.DataFrame) -> float:
    """`cell_id IS NULL` の割合。参加者がカードを無視していないか。"""
    if check_ins.empty:
        return float("nan")
    return float(check_ins["cell_id"].isna().mean())


def anomalies(recommendation_scores: pd.DataFrame, bingo_cells: pd.DataFrame,
              check_ins: pd.DataFrame) -> dict:
    return {
        "assignment_concentration": assignment_concentration(recommendation_scores),
        "empty_cells": empty_cells(bingo_cells),
        "off_card_visit_rate": off_card_visit_rate(check_ins),
    }


# --- 画面4 実験の進捗（03 §4）— 分母だけ ---------------------------------


def experiment_progress(recommendation_scores: pd.DataFrame) -> dict:
    """`attributes.arm` 別の **提示数** と対象参加者数のみ。

    ここで **訪問率を出さない**（03 §5）。当日に A/B の効果が見えると設定を変えたくなり、
    実験が壊れる。訪問件数・訪問率の群別集計はこの関数にも当日画面にも書かない。
    """
    arm = recommendation_scores["attributes"].apply(_arm_of)
    split = recommendation_scores[arm.notna()].assign(arm=arm[arm.notna()])
    if split.empty:
        return {"split_started": False, "by_arm": {}, "n_participants": 0}
    by_arm = split.groupby("arm").size().to_dict()
    return {
        "split_started": True,
        "first_split_at": pd.to_datetime(split["created_at"], utc=True).min(),
        "by_arm": {k: int(v) for k, v in by_arm.items()},
        "n_participants": int(split["user_id"].nunique()),
    }


def _arm_of(attributes) -> str | None:
    """`recommendation_scores.attributes`（JSON or dict）から arm を取り出す。無ければ分割前。"""
    if isinstance(attributes, str):
        import json

        try:
            attributes = json.loads(attributes)
        except (ValueError, TypeError):
            return None
    if isinstance(attributes, dict):
        arm = attributes.get("arm")
        return str(arm) if arm is not None else None
    return None
