# データベースの実態

## 結論

**去年の本番データは `protofes` プロジェクトの `(default)` データベースにある。**

- プロジェクトID: `protofes`
- データベースID: `(default)`
- ロケーション: `asia-northeast2`（大阪）
- 種別: `FIRESTORE_NATIVE`

## GCPプロジェクト一覧

作業アカウントから見えるプロジェクトは4つある。**紛らわしいので注意**。

| プロジェクトID | 内容 | 本分析での扱い |
|---|---|---|
| `protofes` | **去年の本番環境** | **これを使う** |
| `pretofes` | 綴り違い（`pro` / `pre`）。作成時のタイプミスと思われる | 無視 |
| `event-support-app` | 今年のプロジェクト。Firestore 未作成 | 無視 |
| `confident-coder-471201-m4` | 無関係（"My First Project"） | 無視 |

## `protofes` 内のデータベース

```
gcloud firestore databases list --project=protofes
```

| データベースID | 作成日時 | 判定 |
|---|---|---|
| `(default)` | **2025-10-04** | **本番。イベント（10/10-11）の6日前に作成** |
| `protofest-test1` | 2025-10-27 | イベント後のクローン |
| `protofest-test2` | 2025-10-27 | イベント後のクローン |

### `(default)` が本番である根拠

**根拠1: 作成日時。**
`(default)` のみイベント前（10/4）に作られている。`test1` / `test2` はイベント16日後の10/27。

**根拠2: アプリのコード。**
去年のバックエンド `app.py` は Firestore クライアントを引数なしで初期化している。

```python
db = firestore.Client()
```

`database` 引数が無い場合、接続先は `(default)` に固定される。
本番の GAE 上で動いていたのはこのコードであるため、
実データは `(default)` 以外にありえない。

## 件数の実測（2026-08時点）

| コレクション | `(default)` | `test1` | `test2` | 差分 |
|---|---:|---:|---:|---:|
| `users` | **302** | 296 | 296 | +6 |
| `booths` | **40** | 40 | 40 | 0 |
| `user_status` | **123** | 121 | 121 | +2 |
| `checkins` | **2,141** | 2,123 | 2,123 | +18 |
| `awards` | **523** | 510 | 510 | +13 |
| `bingo_card` | **4,704** | 4,640 | 4,640 | +64 |

### この表から分かること

**`test1` と `test2` は完全に同一の内容である。**
10/27に `(default)` から作られた同一のクローンとみられる。

**`(default)` は10/27以降も書き込みを受けている。**
ユーザー6名・チェックイン18件・ビンゴカード4枚（64 ÷ 16）が、
イベント終了から2週間以上経った後に追加されている。

これらはイベント参加者ではなく、**エクスポート機能のデバッグ中に作られたテストアカウント**
と判断する（去年のリポジトリの最終コミットが「データエクスポート(失敗)」であることと整合する）。

**この事実により、テストデータの除外基準が機械的に定まる。**
詳細は [exclusion-rules.md](../03-extraction/exclusion-rules.md) を参照。

## 派生する数値

| 項目 | 値 | 算出 |
|---|---|---|
| ビンゴカード生成者 | **294名** | 4,704 ÷ 16 = 294.0（ちょうど） |
| カード未生成者 | **8名** | 302 − 294 |
| 1人あたり平均チェックイン | **7.09件** | 2,141 ÷ 302 |
| ブースあたり平均訪問者 | **53.5名** | 2,141 ÷ 40 |
| 投票確定者 | **123名（40.7%）** | `user_status` の件数 ÷ 302 |

**カード未生成者8名**は、登録したがビンゴ画面を一度も開かなかった参加者である。
カード生成は初回アクセス時に実行される仕様（`app.py` の `get_bingo_booths`）のため、
`bingo_card` が存在しないことは「一度も開いていない」ことを意味する。

**注意**: これらはすべて2日間の合計値・テストデータ込みの値である。
日別分解と除外処理を経た値とは異なる。

## 接続方法

```python
from google.cloud import firestore
db = firestore.Client(project="protofes", database="(default)")
```

認証は ADC（Application Default Credentials）を用いる。

```bash
gcloud auth application-default login
```

### quota project について

`protofes` は先輩世代が作成したプロジェクトであり、
`serviceusage.services.use` 権限が無い場合がある。
その場合 quota project の設定は失敗するが、**データの読み取りには影響しない**。
自分が所有するプロジェクトを quota project に指定しておけばよい。

```bash
gcloud auth application-default set-quota-project event-support-app
```
