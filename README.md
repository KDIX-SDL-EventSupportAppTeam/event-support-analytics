# event-support-analytics

2025年「第3回プロトフェス」の来場者行動データを分析し、
2026年のイベント支援アプリの設計判断を根拠づけるためのリポジトリ。

## ドキュメント

| 読むもの | 内容 |
|---|---|
| [docs/FINDINGS.md](docs/FINDINGS.md) | **分析結果サマリ**。5つの問いへの回答と全指標の実測値 |
| [docs/PURPOSE.md](docs/PURPOSE.md) | **このリポジトリの存在意義**。最初に読む |
| [docs/specs/analytics-pipeline/](docs/specs/analytics-pipeline/) | 去年（2025年）データの分析の仕様 |
| [docs/specs/recommendation-evaluation/](docs/specs/recommendation-evaluation/) | 今年（2026年）の推薦の当日監視・事後分析の仕様 |
| [AGENTS.md](AGENTS.md) | 作業指針・絶対規則 |

## 使い方

```bash
pip install -r requirements.txt

# 1. 抽出（Firestoreへは一度だけ接続。ADC認証が必要）
python src/dump_firestore.py

# 2. 中間テーブル生成 → 指標算出 → 図表生成（以降はローカルファイルのみで完結）
python src/run_pipeline.py

# 3. GUIで絞り込みながら確認する（3画面の統合アプリ）
streamlit run src/app.py
```

### GUI ダッシュボード

`streamlit run src/app.py` でブラウザが開く。**左のメニューで3画面を行き来する。**

| 画面 | 対象 | 内容 |
|---|---|---|
| 📊 去年の行動データ | 2025年 | 絞り込みながら分析結果を読む（既定） |
| 🚦 推薦の当日監視 | 2026年 当日 | 信号機。壊れていないかを見る |
| 📈 推薦の事後分析 | 2026年 事後 | 問い1つに図1つ |

合言葉の確認は入口（`src/app.py`）で1回だけ行う。**通るまでメニュー自体が出ない。**
URL で共有する手順（Cloud Run + 合言葉）は [docs/operations/deploy.md](docs/operations/deploy.md) を参照。

個別の画面だけを開くこともできる（開発用）:
`streamlit run src/dashboard.py` / `src/live_dashboard.py` / `src/post_analysis.py`

> **この画面の読み手はエンジニアである。** 当初は運営メンバー（非エンジニア）が
> 単独で読めることを優先して作られていたが、2026-08-31 に切り替わった。
> 平易な言い換えや用語解説が残っているのはその名残である。

- **絞り込み**: 開催日（両日／金／土）、年代、性別、興味ジャンル、
  チェックイン0件・単発訪問者の包含/除外、運営・出展者の pid 除外
- **タブ**: 5つの判断ごとに1タブ＋推薦マスの効果＋クールタイム（前提確認）＋除外候補
- **自動判定**: 絞り込み結果を
  [判断基準](docs/specs/analytics-pipeline/01-context/decision-criteria.md) の事前固定しきい値に
  当てはめて表示する（例: 30分未満が34.7% →「二層構造」）

ローカルの `data/tables/` を読むだけで、個人データを含むファイルを新たに生成しない。
該当者が3名未満になる絞り込みでは、属性の組み合わせから個人が推定されうるため
集計を表示しない（[プライバシー方針](docs/specs/analytics-pipeline/03-extraction/privacy-policy.md)）。

除外規則2（運営・出展者候補）の一覧だけを確認したい場合:

```bash
python src/build_tables.py data/raw/dump_YYYYMMDD_HHMMSS.json --show-staff-candidates
```

`run_pipeline.py` に `--exclude-pids` を渡すと、除外した pid・件数は `output/extraction_stats.json`
（既定パス。`--stats-out` で変更可）に記録される。

### 今年（2026年）の推薦の評価

仕様は [docs/specs/recommendation-evaluation/](docs/specs/recommendation-evaluation/)。
本番 MySQL は**さくらプロキシの読み取り専用の口**から読む（[ADR 0001](docs/decisions/adrs/0001-今年のデータ取得はプロキシの読み取り専用の口を使う.md)）。
口はまだ用意されていないため、既定は合成データで動かす。

```bash
# 合成データを生成（リハーサル用）
python src/synth_rec_data.py --out data/synth
python src/synth_rec_data.py --out data/synth_dead --recommender-dead --no-ops-state

# 統合アプリから開く（左のメニューで「推薦の当日監視」「推薦の事後分析」へ）
streamlit run src/app.py

# 個別に開く場合（開発用）
streamlit run src/live_dashboard.py
streamlit run src/post_analysis.py
```

- 算出式は `src/live_metrics.py` / `src/post_eval_metrics.py` にだけ書く（二重管理しない）
- 当日画面は **A/B の効果（群別訪問率・その差）を表示しない**（仕様 03 §5）
- `interest_match` は凍結値を使い再計算しない（仕様 04 §4）
- 条件属性の定義は `event-support-recommend/features/` を import する。コピーしない
  （`REC_FEATURES_PATH` にそのリポジトリのルートを渡す。`src/rec_features.py`）
- 本番接続は `src/rec_db.py` の `SqlSource`。`REC_READONLY_PROXY_URL` / `REC_READONLY_PROXY_KEY` を
  設定すると本番を読む（**書き込み可能な `SAKURA_PROXY_*` は使わない**）。MySQL 直結は不可能

テスト（合成データのみを使用。実データやFirestore接続は不要）:

```bash
python -m pytest tests/
```

## 現在の状態

**去年データの分析**: 抽出（`dump_firestore.py`）・中間テーブル生成（`build_tables.py`）・
指標算出（`metrics.py`）・可視化（`visualize.py`）を実装済み。
まだ Firestore からの実データ抽出は行っていない
（GCP接続はローカル環境からの一度限りの実行を想定しているため）。

**今年の推薦の評価**: 指標（`live_metrics.py` / `post_eval_metrics.py`）・
画面（`live_dashboard.py` / `post_analysis.py`）・合成データ生成（`synth_rec_data.py`）を実装済み。
本番 MySQL への接続（`rec_db.SqlSource`）も実装済みで、**読み取り専用の口が用意されれば
環境変数2つを設定するだけで動く**（[ADR 0001](docs/decisions/adrs/0001-今年のデータ取得はプロキシの読み取り専用の口を使う.md)）。
ただし**実データはまだ1件も通っていない**。現状は合成データでのリハーサルまで通せる。

## 関連リポジトリ

| リポジトリ | 関係 |
|---|---|
| `KDIX-SDL-EventSupportAppTeam/event-support-server` | 今年のバックエンド。MySQL スキーマの正本 |
| `KDIX-SDL-EventSupportAppTeam/event-support-recommend` | 今年の推薦エンジン。`features/` を import して使う・`/ops/state` の提供元 |
| `KDIX-SDL-EventSupportAppTeam/event-support-frontend` | 今年のフロントエンド |
| `HidetsuguSuto/2025_P3_supporters_game` | 去年のアプリ（**読み取り専用**） |

## 注意

`data/` 以下には302名分の行動履歴が置かれる。**コミットしないこと**
（`.gitignore` で除外済み）。
詳細は [プライバシー方針](docs/specs/analytics-pipeline/03-extraction/privacy-policy.md)。
