# データ源 — 今年のデータはどこに、どんな形であるか

去年は Firestore からの一度限りのダンプだった（[analytics-pipeline](../analytics-pipeline/)）。
**今年は MySQL であり、当日はライブで読む必要がある。** 前提がまったく違う。

---

## 1. 監視を推薦エンジンに依存させない（最重要）

> **推薦エンジンが死んでいるとき、推薦エンジンの `/ops/state` も死んでいる。**

当日の監視は**DB を主たる情報源とする。**

幸いサーバーが `card_unlock_events.strategy` に
「推薦が使えたか（`RECOMMEND`）／フォールバックしたか（`FALLBACK_COVERAGE`）」を記録している。
**DB を読むだけで推薦エンジンの生死が分かる。**

| 情報源 | 何が分かるか | 落ちたときの扱い |
|---|---|---|
| **MySQL** | 推薦が使えたか、フェーズ、決定表件数、割当の分布、評価回収率 | 監視が成立しない（その時はアプリも止まっている） |
| 推薦エンジン `/ops/state` | γ、規則数、規則の被覆率、応答時間 | **その欄だけ「取得不能」と表示して続行する** |
| 推薦エンジンの JSONL ログ | 入出力の全体、規則の本体 | 事後分析でのみ使う |

**`/ops/state` が取れないことをもって画面全体を落とさない。**

---

## 2. MySQL のテーブル

正本は `event-support-server` の `docs/reference/database.md` および
`docs/specs/bingo-dynamic-unlock/02-data-model/schema-changes.md`。

### 当日の監視に使うもの

| テーブル | 使う列 | 何に使うか |
|---|---|---|
| `card_unlock_events` | `strategy`, `phase`, `decision_table_size`, `global_checkin_count`, `created_at` | **フォールバック率・フェーズ・解放数** |
| `check_ins` | `user_id`, `booth_id`, `cell_id`, `visit_order`, `checked_in_at` | チェックイン数、カード外訪問 |
| `booth_ratings` | `checkin_id`, `rating`, `scale`, `rated_at` | **評価回収率** |
| `recommendation_scores` | `booth_id`, `was_assigned`, `attributes` | **割当の集中度**（人気順への退化の検出） |
| `bingo_cells` | `booth_id`, `is_revealed`, `is_achieved`, `source` | 空マス・自己修復の検知 |
| `users` | `id`, `role` | **`role <> 'participant'` を全集計から除外** |

### 事後の分析に追加で使うもの

| テーブル | 使う列 |
|---|---|
| `user_survey_answers` | `age_range`, `occupation`, `industry`, `custom_answers`（`interest_categories` ほか） |
| `booths` / `categories` / `booth_tags` | `category_id`, `name`, `tag` |
| `recommendation_scores` | `score`, `rank_in_event`, `interest_match`, `attributes`, `reason_payload` |

### 取得してはならないもの

`users.email` / `users.password_hash`。**分析に不要であり、渡さないことで事故を防ぐ**
（[rules/data-handling.md](../../rules/data-handling.md)）。

---

## 3. `recommendation_scores.attributes` の中身

推薦エンジンが JSON で書き込む。**サーバーは中身を解釈しない。**
定義の正本は `event-support-recommend/docs/specs/02-features.md`。

```jsonc
{
  "v": 1,                                    // スキーマ版。当日に属性構成を変えても識別できる
  "strategy": "DRSA",
  "arm": "DRSA",                             // ★実験の割付。"DRSA" / "COVERAGE"
  "split_seed": "…",
  "enabled": ["preference_match", "rating_affinity"],
  "condition": { "preference_match": 3, "rating_affinity": 2 },
  "raw": { "declared": 2, "behavioral": 1, "category_id": "…", "visitor_count": 12 }
}
```

- **`arm` が実験の割付そのものである。** これが無い行は分割前（品質ゲート通過前）
- `v` が混在しうる。**集計時は必ず `v` で分けて確認する**
- `reason_payload.rules[].id` に発火した規則の id が入る。**規則の本体は入っていない**（§5）

---

## 4. 接続経路 — 未確定

**今年の MySQL への接続方法が決まっていない。**

サーバーは本番でさくら上の PHP ラッパー API（`SAKURA_PROXY_URL`）経由で接続しており、
**1リクエスト = 1SQL、トランザクション無し**という制約がある。

このリポジトリが、

- プロキシ経由で読むのか
- MySQL へ直接接続するのか（Cloud Run / ローカルからの経路・IP 制限）
- 読み取り専用の資格情報を発行できるのか

が未決である。

> **これは推薦エンジンのスナップショット取得（`event-support-recommend` ADR 0002）と
> まったく同じ問題である。片方を解決すれば両方に使えるので、まとめて決める。**

決まるまでは、**当日ダッシュボードは実装できない**（[03](03-live-dashboard.md)）。
事後の分析は、イベント後のダンプ1回でも成立する。

---

## 5. 推薦エンジンの JSONL ログ

推薦エンジンが標準出力へ吐き、Cloud Logging に残る。**事後分析でのみ使う。**

| 種別 | 1行の意味 | 用途 |
|---|---|---|
| `recommend` | 1回の推薦の入出力の全体 | 個票の再構成、リプレイ |
| `rules_built` | 規則の再生成1回ぶん（**規則の本体を含む**） | **「その時刻にどんな規則が存在したか」の復元** |
| `snapshot_built` | スナップショット取得1回ぶんの要約 | 決定表の成長曲線 |

### `rules_built` が無いと困ること

規則は5分ごとに作り直される。`recommendation_scores.reason_payload` には
**規則 id と要約しか入らない**（3万行になるため本体は入れられない）。

**このログが無いと、論文に「実際に抽出された規則」を載せられない。**
id だけが残って中身が分からない状態になる。

### 回収経路をイベント前に通しておく

Cloud Logging の中でしか読めない状態は、実質使えないのと同じである。

```
gcloud logging read → JSONL ファイル → data/raw/
```

**当日の夜に初めて試すことにしない。** リハーサルで一度通す。

---

## 6. 去年データとの接続

去年のデータは既存の `data/tables/*.csv`（`participants` / `visits` / `booths`）にある。

**今年のデータと同じ形に揃える必要はない。** 比較に使うのは限られた指標だけである。

| 比較する指標 | 去年 | 今年 |
|---|---|---|
| 訪問ブース数の分布 | `participants.n_booths` | `check_ins` の集計 |
| 推薦マスへの訪問率 | `n_rec_hit / n_rec_total` | `recommendation_scores` × `check_ins` |
| 不一致カテゴリへの自発訪問率 | **ブースへのカテゴリ手作業付与が必要**（[01](01-context.md) §3.1） | `interest_match = MISMATCH` |

**去年の比較は「ユーザー × 日」単位、今年は1日開催なので単純**。
去年は金曜（192名・1,367件）を代表日として使う（[FINDINGS.md](../../FINDINGS.md) 制約4）。
