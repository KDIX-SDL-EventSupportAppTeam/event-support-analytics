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
| `users` | `id`, `role` | **`role = 'participant'` だけを残す**（規則は [05](05-exclusion-policy.md)） |

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

## 4. 接続経路 — さくらプロキシの読み取り専用の口（ADR 0001）

**決定済み**（[ADR 0001](../../decisions/adrs/0001-今年のデータ取得はプロキシの読み取り専用の口を使う.md)、案A′）。

本番（さくら Standard）は**外部から MySQL へ直接接続できない**。
サーバーはさくら上の PHP ラッパー API（プロキシ）経由で読み書きしている。
このリポジトリは、そこに**読み取り専用の口を1つ足したもの**を使う。
書き込み可能な `SAKURA_PROXY_*` の鍵はこのリポジトリに持ち込まない。

実装は `src/rec_db.py` の `SqlSource`。

### 契約

正本は `event-support-server/src/db/http-proxy.ts`。

```
POST <base_url>
  headers: { "Content-Type": "application/json", "X-Proxy-Key": <key> }
  body:    { "sql": "<SQL文字列>", "params": [...] }
  →  200: { "rows": [...], "affectedRows": n, "insertId": n|null }
```

### 口の設置

口の PHP・権限の SQL・検証一式は [`deploy/sakura-readonly-proxy/`](../../../deploy/sakura-readonly-proxy/)
にある。**Docker で MySQL と PHP を立てて通しきってあるので、先生に渡すのは完成品である。**

```bash
python deploy/sakura-readonly-proxy/verify/verify.py   # 本番には触らない
```

### 環境変数

| 変数 | 意味 |
|---|---|
| `REC_READONLY_PROXY_URL` | 読み取り専用の口の URL。ここへ直接 POST する |
| `REC_READONLY_PROXY_KEY` | その口の鍵。**読み取り専用ユーザーに紐づくもの** |

**`SAKURA_PROXY_URL` / `SAKURA_PROXY_KEY` は使わない。** あれは全権であり、
分析から `UPDATE` が通ってしまう（ADR 0001 案A の欠点）。両者は別物として名前を分けてある。

未設定なら `SqlSource()` は構築時に落ちる。**口が用意され次第、この2つを設定するだけで動く。**

### 制約と、その受け止め方

| 制約 | 実装での扱い |
|---|---|
| **1リクエスト = 1SQL。** トランザクションも行ロックも無い | テーブルを1枚ずつ読み、結合は pandas 側（`load_tables`）で行う。当日の45秒ごとの更新では、テーブル間で最大数秒のずれが生じうる |
| **エラーはすべて HTTP 500 に潰れる**（サーバー ADR 0001）。MySQL のエラーコードは取れない | 原因は判別できない前提で、**どのテーブルを読もうとしたか**だけを例外に残す。**SQL 本文と鍵は出さない** |
| サーバー側のタイムアウトは30秒 | クライアント側は既定20秒。当日画面の更新間隔（45秒）を1テーブルで食い潰さないため |
| パラメータの変換（boolean → 0/1、Date → `'YYYY-MM-DD HH:MM:SS'`）はプロキシが行う | 読み取りのみなのでパラメータは使わない（`params: []`）。返る `DATETIME` は **UTC** として `pd.to_datetime(utc=True)` で揃える |

### 読み取り専用の担保（多重）

権限（読み取り専用ユーザー）が最終的な保証だが、**クライアント側でも同じことを拒む**。

1. テーブル名は `LIVE_TABLES` / `POST_TABLES` の**キーのみ**。任意の文字列を SQL に入れない
2. 列も同じ定義から組み立てる。**`SELECT *` を書かない**ので、
   `users.email` / `users.password_hash` は**構造的に要求できない**
3. 組み立てた SQL が `SELECT` 1文でなければ送信前に拒否する

### 失敗しても画面を落とさない

当日監視は45秒ごとに更新される。取得の失敗は `RuntimeError` で上がり、
`live_dashboard.render()` がそれを捕まえて**その回の描画だけを諦める**（次の更新で復帰しうる）。
`/ops/state` の扱い（§1）と同じ思想である。

### 事後分析はこの経路を待たない

イベント後のダンプ1回で足りる。CSV を `data/` に置いて `DumpSource` で読む
（ADR 0001「影響」）。

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
