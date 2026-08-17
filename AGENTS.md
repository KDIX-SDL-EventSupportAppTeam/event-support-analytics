# AGENTS.md

## このリポジトリについて

**まず [docs/PURPOSE.md](docs/PURPOSE.md) を読むこと。** リポジトリの存在意義と、
最終的に答えるべき5つの問いが書かれている。

一行で言えば、**2025年の第3回プロトフェスで蓄積された来場者行動データを分析し、
2026年のイベント支援アプリの設計判断を根拠づけるためのリポジトリ**である。

## 仕様書

仕様は `docs/.sdd/` 以下に意味単位で分割されている。
**実装より先に仕様を確定させ、実装は仕様に従う。**

全体像と読む順序は [docs/.sdd/README.md](docs/.sdd/README.md) を参照。

初見であれば、最低限この3つを読んでから作業を始めること。

1. [docs/.sdd/01-context/background.md](docs/.sdd/01-context/background.md) — 去年と今年の差分
2. [docs/.sdd/02-data-source/firestore-schema.md](docs/.sdd/02-data-source/firestore-schema.md) — データ構造
3. [docs/.sdd/04-analysis/confounders.md](docs/.sdd/04-analysis/confounders.md) — **交絡要因。これを読まずに結果を解釈しない**

## 作業上の絶対規則

### データの扱い

- **メールアドレスとパスワードハッシュを保存しない。**
  抽出時に仮名ID（`u0001` 形式）へ置換する。対応表は生成しない
- **`data/` をコミットしない。** 302名分の行動履歴はバージョン管理下に置かない
- 集計済みの結果（`output/`）はコミットしてよい

詳細: [docs/.sdd/03-extraction/privacy-policy.md](docs/.sdd/03-extraction/privacy-policy.md)

### GCPへのアクセス

- **Firestore へのアクセスは抽出時の一度だけ。** 以降はローカルファイルのみで作業する
- **読み取り専用。** 書き込みAPIを呼ぶコードを書かない
- 接続先は `protofes` プロジェクトの `(default)` データベース
  （`protofest-test1` / `test2` はイベント後のクローンであり本番ではない）

詳細: [docs/.sdd/02-data-source/database-inventory.md](docs/.sdd/02-data-source/database-inventory.md)

### 去年のリポジトリ

`HidetsuguSuto/2025_P3_supporters_game` は先輩世代の資産である。
push 権限はあるが、**読み取り専用として扱い、一切変更しない。**

## 間違えやすい点（頻出）

| 落とし穴 | 正しい扱い |
|---|---|
| タイムスタンプは UTC 保存 | 必ず JST(+9) へ変換してから日付判定する |
| 去年は2日開催、今年は1日 | 分析単位は「ユーザー × 日」。302名は延べ人数 |
| クールタイムが期間中に変更された | チェックイン間隔の下限が動く。データから変更点を検出する |
| 同一ブースへの再訪問は記録されない | ユニーク訪問数とチェックイン総数は常に一致 |
| ブースIDは丸数字（`①`〜`㊶`） | Unicode 上で不連続。ソートには `booth_no`（整数）を使う |
| ブース数は42ではなく **40** | 21番と42番は欠番。去年のコードのコメントが誤り |

## 環境

- Python 3.13
- `google-cloud-firestore` 2.21.0
- 認証は ADC（`gcloud auth application-default login`）

## 現在の状態

`src/` 以下に一通りの実装が揃っている。

| スクリプト | 役割 | 仕様 |
|---|---|---|
| `dump_firestore.py` | Firestoreからの一括抽出・仮名化 | [dump-spec.md](docs/.sdd/03-extraction/dump-spec.md) |
| `build_tables.py` | 中間テーブル生成・除外規則の適用 | [intermediate-tables.md](docs/.sdd/04-analysis/intermediate-tables.md) / [exclusion-rules.md](docs/.sdd/03-extraction/exclusion-rules.md) |
| `metrics.py` | 指標カタログ(A〜F)・クールタイム床検出 | [metrics-catalog.md](docs/.sdd/04-analysis/metrics-catalog.md) / [confounders.md](docs/.sdd/04-analysis/confounders.md) |
| `visualize.py` | 判断に直結する図表の生成 | [chart-spec.md](docs/.sdd/05-visualization/chart-spec.md) |
| `run_pipeline.py` | 2〜4を一括実行するエントリポイント | — |

**まだ Firestore からの実データ抽出・実行検証は行っていない。**
`tests/` の合成データによるユニットテストのみで検証済み。
実データで実行した際、仕様上「実データを見てから確定する」とされている項目
（[confounders.md](docs/.sdd/04-analysis/confounders.md) 末尾の表）に
食い違いが出た場合は、該当する仕様書を更新すること。
