# 仕様書（正本）

| 機能 | 状態 | 内容 |
|---|---|---|
| [analytics-pipeline/](analytics-pipeline/) | 実装済み | **2025年**（去年）Firestore データの抽出・中間テーブル・指標・可視化 |
| [recommendation-evaluation/](recommendation-evaluation/) | 草案 | **2026年**（今年）推薦システムの当日監視と事後分析 |

`recommendation-evaluation/` はこのリポジトリの守備範囲の拡張である
（去年データの分析 → 今年データの監視・評価）。
[PURPOSE.md](../PURPOSE.md) への反映が必要
（[recommendation-evaluation/README.md](recommendation-evaluation/README.md) E-4）。

**実装より先に仕様を確定させ、実装は仕様に従う。**
実データで実行して仕様と食い違いが出たら、該当する仕様書を更新する。
