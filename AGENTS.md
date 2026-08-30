# event-support-analytics

2025年 第3回プロトフェスの来場者行動データを分析し、2026年のアプリの設計判断を根拠づける。
**分析結果だけ知りたいなら [docs/FINDINGS.md](docs/FINDINGS.md) を読めばよい。**

## 最初に読むもの

| 知りたいこと | 見る場所 |
|---|---|
| このリポジトリの存在意義 | [docs/PURPOSE.md](docs/PURPOSE.md) |
| 分析結果 | [docs/FINDINGS.md](docs/FINDINGS.md) |
| 仕様 | [docs/specs/](docs/specs/README.md) |
| 守ること | [docs/rules/](docs/rules/README.md) — Git・データの扱い・ドキュメント |
| 動かし方 | [README.md](README.md) · [docs/operations/](docs/operations/README.md) |

画面は `streamlit run src/app.py` で3つまとめて起動する（去年の行動データ／推薦の当日監視／
推薦の事後分析）。**読み手はエンジニアである**（2026-08-31 に運営メンバーから切り替わった）。

着手前に最低限この3つを読む。

1. [背景](docs/specs/analytics-pipeline/01-context/background.md) — 去年と今年の差分
2. [Firestore スキーマ](docs/specs/analytics-pipeline/02-data-source/firestore-schema.md)
3. [交絡要因](docs/specs/analytics-pipeline/04-analysis/confounders.md) — **これを読まずに結果を解釈しない**

## 絶対に守ること

1. **個人データを保存しない。** 仮名 ID へ置換し、対応表を作らない。`data/` はコミットしない
2. **Firestore は読み取り専用、接続は抽出時の一度だけ**（[rules/data-handling.md](docs/rules/data-handling.md)）
3. **`main` を直接触らない。** 作業ブランチ → `develop` へ PR（[rules/git.md](docs/rules/git.md)）
4. **指標の算出式は `metrics.py` にだけ書く。** GUI・図表から呼ぶ。二重管理しない

## 間違えやすい点

| 落とし穴 | 正しい扱い |
|---|---|
| タイムスタンプは UTC 保存 | 必ず JST(+9) へ変換してから日付判定する |
| 去年は2日開催、今年は1日 | 分析単位は「ユーザー × 日」。302名は延べ人数 |
| クールタイムが期間中に変更された | チェックイン間隔の下限が動く。データから変更点を検出する |
| 同一ブースへの再訪問は記録されない | ユニーク訪問数とチェックイン総数は常に一致 |
| ブース ID は丸数字（`①`〜`㊶`） | Unicode 上で不連続。ソートには `booth_no`（整数）を使う |
| ブース数は42ではなく **40** | 21番と42番は欠番。去年のコードのコメントが誤り |

## 環境

Python 3.13 / `google-cloud-firestore` 2.21.0 / 認証は ADC（`gcloud auth application-default login`）
