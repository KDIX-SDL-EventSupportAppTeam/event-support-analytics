# 中間テーブル

生データ（JSON）を、分析可能な平坦なテーブルに正規化する。
**すべての集計はこの3テーブルから行う。**個別に生データを触らない。

## 設計原則

**分析単位は「ユーザー × 日」である。**

去年は2日間開催のため、ユーザー単位で集計すると
両日参加者の滞在時間が24時間超になり、分布が壊れる。
`day` を主キーに含めることでこれを構造的に防ぐ。

## visits

チェックイン1件が1行。全分析の土台。

| カラム | 型 | 定義 |
|---|---|---|
| `pid` | string | 仮名ユーザーID（`u0001`） |
| `booth_id` | string | ブースID（丸数字） |
| `booth_no` | int | ブース番号（整数。ソート用） |
| `ts_jst` | datetime | チェックイン時刻（**JST**） |
| `day` | date | 開催日（`2025-10-10` / `2025-10-11`） |
| `visit_seq` | int | その参加者のその日における訪問順（1始まり） |
| `gap_min` | float | 直前のチェックインからの経過分。`visit_seq = 1` では `null` |

**主キー**: (`pid`, `booth_id`)
去年の仕様上、同一ユーザーが同一ブースに2回チェックインすることはないため、
`day` を含めなくても一意になる。

### 生成時の注意

- `ts_jst` は UTC から必ず変換する（[firestore-schema.md](../02-data-source/firestore-schema.md) 参照）
- `day` は JST 変換**後**の日付で判定する
- `visit_seq` / `gap_min` は (`pid`, `day`) でグループ化し、`ts_jst` 昇順で計算する
- 日を跨いで `gap_min` を計算しない（両日参加者で巨大な値が発生する）

## participants

「参加者 × 日」が1行。`visits` からの派生。

| カラム | 型 | 定義 |
|---|---|---|
| `pid` | string | 仮名ユーザーID |
| `day` | date | 開催日 |
| `age` | string | 年代（`users` より） |
| `gender` | string | 性別 |
| `genre` | string | 興味ジャンル |
| `first_ts` | datetime | その日の初回チェックイン時刻（JST） |
| `last_ts` | datetime | その日の最終チェックイン時刻（JST） |
| `dwell_min` | float | 滞在時間 = `last_ts − first_ts`（分）。単発訪問者は 0 |
| `n_booths` | int | その日の訪問ブース数 |
| `is_single` | bool | 単発訪問者フラグ（`n_booths == 1`） |
| `n_card` | int | ビンゴカードのマス数（通常16。未生成なら0） |
| `n_card_hit` | int | カード16マスのうちチェックイン済みの数 |
| `n_rec_hit` | int | 推薦マスのうちチェックイン済みの数 |
| `n_rec_total` | int | 推薦マスの総数（通常4、フォールバック時は0） |
| `bingo_lines` | int | 成立したビンゴのライン数（0〜10） |
| `voted` | bool | アワード投票を行ったか |
| `vote_finalized` | bool | 投票を確定したか |

**主キー**: (`pid`, `day`)

### チェックイン0件ユーザーの扱い

**`visits` に現れないユーザーも、`participants` には行を持たせる。**

`users` に存在するが `checkins` が0件のユーザーは、
`day` を `null`、`n_booths` を 0、`dwell_min` を `null` として1行だけ作る。

これを省くと「アプリを使わなかった層」が集計から消える。
[unavailable-data.md](../02-data-source/unavailable-data.md) および
判断3に直結するため、必ず含めること。

### bingo_lines の算出

`bingo_card` の `position`（0〜15）を4×4グリッドに復元し、
横4本・縦4本・対角2本の計10本について、
全マスが `checkins` に含まれるかを判定する。

去年のアプリの `check_for_bingo` と同じロジックだが、
アプリ側はコイン付与を4ラインで打ち切っている。
**分析では打ち切らず、実際の成立ライン数を記録する。**

## booths

ブース1件が1行。40行。

| カラム | 型 | 定義 |
|---|---|---|
| `booth_id` | string | ブースID（丸数字） |
| `booth_no` | int | ブース番号（1〜41、21欠番） |
| `booth_name` | string | プロトタイプ名 |
| `booth_short` | string | 短縮名（グラフのラベル用。手動作成） |
| `booth_description` | string | 説明文 |
| `booth_emoji` | string | 絵文字 |
| `map_x` / `map_y` | float | 会場マップ上の座標。**手入力。当面は未設定** |
| `zone` | string | 会場エリア区分。**手入力。当面は未設定** |

### booth_short について

ブース名には極端に長いものがある（㉚ は60文字超）。
グラフの軸ラベルには使えないため、短縮名を別途用意する。

形式: `㉚ 海洋プラ→フライングディスク` のように、
ブース番号と20文字以内の要約を組み合わせる。

### map_x / map_y / zone について

**当面は空のままでよい。**
D-1〜D-3（訪問者数の順位・倍率・集中度）を先に算出し、
偏りが実際に存在することを確認してから手入力する。

偏りが小さい場合、この作業自体が不要になる
（[unavailable-data.md](../02-data-source/unavailable-data.md) 参照）。

## テーブル間の関係

```
booths ──< visits >── participants
             (booth_id)   (pid, day)
```

- `visits.booth_id` → `booths.booth_id`
- `visits.(pid, day)` → `participants.(pid, day)`

## 出力形式

CSV とする（`data/tables/`）。

- 文字コード: UTF-8（BOM付き。Excel で直接開けるようにするため）
- 日時: ISO8601（`2025-10-10T13:45:00+09:00`）
- 欠損: 空文字

`data/` は `.gitignore` に含める。
