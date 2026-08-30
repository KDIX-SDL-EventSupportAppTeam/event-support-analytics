# 抽出仕様

## 基本方針

**GCPへのアクセスは一度だけ。以降はローカルファイルのみで作業する。**

分析は必ず試行錯誤を伴う。そのたびに Firestore を叩く理由はない。
一度全件をローカルに落とし、それを唯一の入力として扱う。

これにより:
- 分析の再現性が保証される（同じ入力から同じ結果）
- ネットワークと認証が以降不要になる
- 誤って本番データに書き込む事故が構造的に起きない

## 取得方法: コレクショングループクエリ

### やってはいけない実装

去年のアプリはこのパターンで書かれている。

```python
for user in db.collection('users').stream():          # 302回
    for c in user.reference.collection('checkins').stream():   # 302回のRTT
        ...
```

ユーザー数ぶんラウンドトリップが発生する。302名なら303回の往復になる。

### 採用する実装

Firestore のコレクショングループクエリを使い、階層を無視して一括取得する。

```python
for doc in db.collection_group('checkins').stream():
    d = doc.to_dict()                          # user_id, booth_id, timestamp
    owner = doc.reference.parent.parent.id     # users/{email} の email
```

`checkins` / `awards` / `bingo_card` の3つすべてこの方法で取得する。

**`bingo_card` は `user_id` フィールドを持たないため、
`doc.reference.parent.parent.id` による親の復元が必須。**

### インデックスについて

絞り込み無しの全件 `stream()` であればインデックスは不要。
`where` や `order_by` を併用する場合のみ必要になる。

`FAILED_PRECONDITION` が出た場合、エラーメッセージに
インデックス作成用のURLが含まれている。

## 読み取り量とコスト

| コレクション | 件数 |
|---|---:|
| `users` | 302 |
| `booths` | 40 |
| `user_status` | 123 |
| `checkins` | 2,141 |
| `awards` | 523 |
| `bingo_card` | 4,704 |
| **合計** | **約7,800リード** |

Firestore の無料枠は50,000リード/日。**課金は発生しない。**

## 出力仕様

### ディレクトリ構成

```
data/
├── raw/
│   └── dump_YYYYMMDD_HHMMSS.json    # 抽出直後の生データ（仮名化済み）
└── tables/
    ├── visits.csv                    # 中間テーブル
    ├── participants.csv
    └── booths.csv
```

`data/` は `.gitignore` に含める。**データ本体はリポジトリにコミットしない。**

### 生データのフォーマット

単一の JSON ファイルとする（全体で数MB程度のため分割不要）。

```json
{
  "meta": {
    "dumped_at": "2026-08-17T12:34:56+09:00",
    "project": "protofes",
    "database": "(default)",
    "counts": { "users": 302, "checkins": 2141, ... }
  },
  "booths": [
    { "booth_id": "①", "booth_no": 1, "booth_name": "...", "booth_description": "...", "booth_emoji": "🧂" }
  ],
  "users": [
    { "pid": "u0001", "age": "...", "gender": "...", "genre": "...", "gachapon_coins_spent": 0 }
  ],
  "checkins": [
    { "pid": "u0001", "booth_id": "①", "ts_utc": "2025-10-10T01:23:45Z" }
  ],
  "bingo_card": [
    { "pid": "u0001", "booth_id": "①", "position": 5, "is_recommendation": true }
  ],
  "awards": [
    { "pid": "u0001", "award_name": "...", "booth_id": "①", "ts_utc": "..." }
  ],
  "user_status": [
    { "pid": "u0001", "vote_finalized": 1 }
  ]
}
```

### フィールドの扱い

| 元フィールド | 出力 | 理由 |
|---|---|---|
| `users` のドキュメントID（email） | `pid`（`u0001` 形式）に置換 | 個人情報を保持しない |
| `email` | **出力しない** | 同上 |
| `password_hash` | **取得しない** | 分析に不要。読み出す理由がない |
| `last_checkin_timestamp` | **出力しない** | `checkins` から再計算できる冗長データ |
| `bingo_card` の `booth_name` 等 | **出力しない** | `booths` との重複。結合で復元できる |
| `timestamp` | ISO8601（UTC）文字列 | JSON でのラウンドトリップを保証 |

`booth_id`（丸数字）に加えて、**`booth_no`（整数）を付与する**。
`booth_image_url` のファイル名から抽出する（`/booth_images/22.png` → 22）。
丸数字は Unicode 上で連続していないため、ソートには整数を使う。

## 仮名IDの採番

```
u0001, u0002, ... u0302
```

- メールアドレスの**ソート順**で機械的に採番する（決定的で、再実行しても同じ結果になる）
- 対応表は**生成しない**（不要と決定済み）
- 採番後、メールアドレスはメモリ上からも破棄する

詳細は [privacy-policy.md](privacy-policy.md) を参照。

## 冪等性と安全性

- スクリプトは**読み取り専用**。Firestore への書き込みAPIを一切呼ばない
- 出力ファイル名にタイムスタンプを含め、既存ファイルを上書きしない
- 接続先（プロジェクト・データベース）はコマンドライン引数で指定可能にし、
  デフォルトは `protofes` / `(default)` とする

## 実行前チェック

```bash
gcloud config get-value project      # protofes であること
gcloud firestore databases list --project=protofes
```

抽出後、`meta.counts` が
[database-inventory.md](../02-data-source/database-inventory.md) の実測値と
一致することを確認する。一致しない場合は取得漏れを疑う。
