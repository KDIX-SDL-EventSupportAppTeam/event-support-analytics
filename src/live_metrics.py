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

GREEN, YELLOW, RED, UNKNOWN = "green", "yellow", "red", "unknown"

#: `/ops/state` が認証エラー（401/403）だったことを表す値。
#: **`rec_db.OPS_AUTH_ERROR` と同じ文字列でなければならない。**
#: ここで再定義しているのは、このモジュールを純粋（DataFrame in / dict out）に保つため。
#: 取得層（HTTP・環境変数）へ依存させない。一致は tests/test_live_metrics.py が固定する。
OPS_AUTH_ERROR = "auth"

#: base URL はあるが `RECOMMEND_OPS_TOKEN` が空。リクエストしていない。
#: **`rec_db.OPS_TOKEN_MISSING` と同じ文字列であること。**
OPS_TOKEN_MISSING = "token_missing"

#: 認証トークンの環境変数名。表示に使うだけ。同じく `rec_db` と一致していること。
TOKEN_ENV = "RECOMMEND_OPS_TOKEN"

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


def normalize_ops_state(payload: dict | None) -> dict | None:
    """推薦エンジンの `/ops/state`（入れ子）と合成データ（フラット）を同じ形にそろえる。

    無いキーは None にする。**0 や空文字で埋めない**（「取れていない」と「0」を区別する）。
    """
    if not isinstance(payload, dict):
        return None
    rules = payload.get("rules") if isinstance(payload.get("rules"), dict) else {}
    snap = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    phase = payload.get("phase") if isinstance(payload.get("phase"), dict) else {}
    up, down = rules.get("count_certain_up"), rules.get("count_certain_down")
    n_certain = payload.get("n_certain_rules")
    if n_certain is None and (up is not None or down is not None):
        n_certain = int(up or 0) + int(down or 0)
    gate = phase.get("gate_detail")
    return {
        "gamma": payload.get("gamma", rules.get("gamma")),
        "n_certain_rules": n_certain,
        "rule_coverage": payload.get("rule_coverage", rules.get("candidate_coverage")),
        "latency_p95_ms": payload.get("latency_p95_ms"),
        "phase_current": phase.get("current"),
        "phase_judged": phase.get("judged"),
        "quality_gate_passed": phase.get("quality_gate_passed"),
        "gate_detail": {k: gate.get(k) for k in ("size", "rules", "gamma", "coverage")}
        if isinstance(gate, dict) else None,
        "decision_table_size": snap.get("decision_table_size"),
        "snapshot_built_at": snap.get("built_at"),
        "rules_built_at": rules.get("built_at"),
    }


def ops_state_signals(ops_state: dict | None, status: str | None = None) -> list[Signal]:
    """`/ops/state` 由来の項目（γ・規則本数・被覆率・応答時間）。

    `ops_state` が None のとき、各項目を「取得不能」（level=unknown）で返し、
    **他の指標は止めない**（03「/ops/state が取れないとき」）。

    `status` が `OPS_AUTH_ERROR`（401/403）のときだけ「認証エラー」と出し分ける。
    **当日「トークンの設定漏れ」と「推薦エンジンが落ちている」を画面上で区別するため**であり、
    表示の親切さの話ではない。打てる手がまったく違う（前者は env を直す、後者はエンジンを見る）。
    """
    if ops_state is None:
        auth = status == OPS_AUTH_ERROR
        missing = status == OPS_TOKEN_MISSING
        na = "認証エラー" if auth else "未設定" if missing else "取得不能"
        detail = (f"{na}（{TOKEN_ENV} を確認）" if (auth or missing) else na)
        first_action = (
            f"{TOKEN_ENV} が未設定か誤っている。"
            "推薦サービスの OPS_TOKEN と同じ値を設定する（エンジン自体は生きている可能性が高い）"
            if auth else
            f"{TOKEN_ENV} が未設定。推薦サービスの OPS_TOKEN と同じ値を Cloud Run の env に設定する"
            if missing else
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
    ops_state = normalize_ops_state(ops_state)
    gamma = ops_state.get("gamma")
    n_certain = ops_state.get("n_certain_rules")
    coverage = ops_state.get("rule_coverage")
    p95 = ops_state.get("latency_p95_ms")

    gamma_level = _level(gamma, 0.5, 0.0, higher_is_worse=False)
    if n_certain is not None and n_certain <= 1:
        gamma_level = RED
    if p95 is None:
        latency_signal = Signal("latency_p95", "応答時間 p95", "未提供", UNKNOWN,
                                "/ops/state に latency_p95_ms が無い",
                                "推薦側の応答に無い項目。Cloud Run のメトリクスで見る")
    else:
        latency_signal = Signal("latency_p95", "応答時間 p95", p95, _level(p95, 400, 600, higher_is_worse=True),
                                f"p95 {p95}ms", "タイムアウト（1000ms）が近い。推薦エンジンの負荷を見る")
    return [
        Signal("drsa_quality", "DRSA 品質（γ・確実規則）", gamma, gamma_level,
               f"γ={gamma} / 確実規則 {n_certain}本",
               "品質ゲートが正しく止めているか確認する。しきい値を下げない"),
        Signal("rule_coverage", "規則の被覆率", coverage, _level(coverage, 0.5, 0.3, higher_is_worse=False),
               f"被覆率 {coverage}", "大半の候補が判断保留（score=0.5）になっている可能性。規則生成側を見る"),
        latency_signal,
    ]


def phase_signal(state: dict | None) -> Signal:
    if not state or state.get("phase_current") is None:
        return Signal("ops_phase", "エンジンのフェーズ", "取得不能", UNKNOWN, "取得不能", "/ops/state が取れ次第確認する")
    cur, judged, size = state["phase_current"], state.get("phase_judged"), state.get("decision_table_size")
    level = GREEN if cur != "COVERAGE" else YELLOW
    return Signal("ops_phase", "エンジンのフェーズ", cur, level,
                  f"判定={judged} / 決定表={size if size is not None else '未取得'} / snapshot={state.get('snapshot_built_at') or '—'}",
                  "COVERAGE のままなら決定表が育っていない。推薦側 §1 A-1（データ取り込み）を疑う")


def gate_detail_signal(state: dict | None) -> Signal:
    gate = (state or {}).get("gate_detail")
    if not gate:
        return Signal("quality_gate", "品質ゲート（size/rules/gamma/coverage）", "取得不能", UNKNOWN, "取得不能", "/ops/state が取れ次第確認する")
    failed = [k for k, v in gate.items() if v is False]
    passed = (state or {}).get("quality_gate_passed")
    value = "通過" if passed else ("未通過: " + ", ".join(failed) if failed else "未通過")
    detail = " / ".join(f"{k}={'○' if v else '×' if v is False else '?'}" for k, v in gate.items())
    return Signal("quality_gate", "品質ゲート（size/rules/gamma/coverage）", value, GREEN if passed else YELLOW, detail,
                  "どの項目で落ちているかを記録する（事後の PHASE_DRSA_MIN 見直しの根拠）。しきい値は当日変えない")


def signal_board(unlock_events: pd.DataFrame, check_ins: pd.DataFrame, booth_ratings: pd.DataFrame,
                 ops_state: dict | None, now: pd.Timestamp,
                 ops_status: str | None = None) -> list[Signal]:
    """画面1（信号機）の全項目。この順で縦に並べる。"""
    normalized = normalize_ops_state(ops_state)
    return [
        fallback_rate(unlock_events, now),
        rating_recovery_rate(booth_ratings, check_ins),
        current_phase(unlock_events, now),
        recent_unlock_count(unlock_events, now),
        phase_signal(normalized),
        gate_detail_signal(normalized),
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
