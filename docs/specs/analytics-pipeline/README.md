---
状態: 実装済み
最終更新: 2026-09-02
---

# 仕様書（SDD）の構成

このディレクトリは、`event-support-analytics` の仕様を意味単位で分割したものである。
実装より先に仕様を確定させ、実装は仕様に従う（Spec-Driven Development）。

## 読む順序

| # | ディレクトリ | 扱う内容 |
|---|---|---|
| 01 | [context](01-context/) | なぜこの分析が必要か、何を判断したいか |
| 02 | [data-source](02-data-source/) | データがどこに、どんな形で存在するか |
| 03 | [extraction](03-extraction/) | どう取り出すか、何を捨てるか |
| 04 | [analysis](04-analysis/) | 何をどう算出するか |
| 05 | [visualization](05-visualization/) | どう見せるか、どの図が何を決めるか |

初見の場合は 01 → 02 → 03 の順に読めば、実装に着手できる。
04 / 05 は抽出が完了してから参照すればよい。

## 各ディレクトリの内容

### 01-context — 文脈
- [background.md](01-context/background.md) — イベントの背景、去年と今年の差分
- [decision-criteria.md](01-context/decision-criteria.md) — 数値がどの値ならどう判断するか

### 02-data-source — データ源
- [database-inventory.md](02-data-source/database-inventory.md) — GCPプロジェクトとデータベースの実態
- [firestore-schema.md](02-data-source/firestore-schema.md) — コレクション構造とフィールド定義
- [unavailable-data.md](02-data-source/unavailable-data.md) — **取得できないデータと、その理由**

### 03-extraction — 抽出
- [dump-spec.md](03-extraction/dump-spec.md) — 取得方法と出力形式
- [privacy-policy.md](03-extraction/privacy-policy.md) — 個人情報の扱い
- [exclusion-rules.md](03-extraction/exclusion-rules.md) — テストデータ等の除外基準

### 04-analysis — 分析
- [intermediate-tables.md](04-analysis/intermediate-tables.md) — 中間テーブルのスキーマ
- [metrics-catalog.md](04-analysis/metrics-catalog.md) — 全指標の定義と算出式
- [confounders.md](04-analysis/confounders.md) — **交絡要因と統制方法（最重要）**

### 05-visualization — 可視化
- [chart-spec.md](05-visualization/chart-spec.md) — 図表の仕様と作図原則

## 仕様の更新について

数値が実際に算出された段階で、前提が覆ることがある。
その場合は該当する仕様書を更新し、**何が変わったかを本文中に明記する**こと。
特に [confounders.md](04-analysis/confounders.md) は、実データを見てから確定する項目を含む。
