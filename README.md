# event-support-analytics

2025年「第3回プロトフェス」の来場者行動データを分析し、
2026年のイベント支援アプリの設計判断を根拠づけるためのリポジトリ。

## ドキュメント

| 読むもの | 内容 |
|---|---|
| [docs/PURPOSE.md](docs/PURPOSE.md) | **このリポジトリの存在意義**。最初に読む |
| [docs/.sdd/](docs/.sdd/) | 仕様書（意味単位で分割） |
| [AGENTS.md](AGENTS.md) | 作業指針・絶対規則 |

## 使い方

```bash
pip install -r requirements.txt

# 1. 抽出（Firestoreへは一度だけ接続。ADC認証が必要）
python src/dump_firestore.py

# 2. 中間テーブル生成 → 指標算出 → 図表生成（以降はローカルファイルのみで完結）
python src/run_pipeline.py
```

除外規則2（運営・出展者候補）の一覧だけを確認したい場合:

```bash
python src/build_tables.py data/raw/dump_YYYYMMDD_HHMMSS.json --show-staff-candidates
```

テスト（合成データのみを使用。実データやFirestore接続は不要）:

```bash
python -m pytest tests/
```

## 現在の状態

抽出（`dump_firestore.py`）・中間テーブル生成（`build_tables.py`）・
指標算出（`metrics.py`）・可視化（`visualize.py`）を実装済み。

まだ Firestore からの実データ抽出は行っていない
（GCP接続はローカル環境からの一度限りの実行を想定しているため）。

## 関連リポジトリ

| リポジトリ | 関係 |
|---|---|
| `KDIX-SDL-EventSupportAppTeam/event-support-server` | 今年のバックエンド |
| `KDIX-SDL-EventSupportAppTeam/event-support-frontend` | 今年のフロントエンド |
| `HidetsuguSuto/2025_P3_supporters_game` | 去年のアプリ（**読み取り専用**） |

## 注意

`data/` 以下には302名分の行動履歴が置かれる。**コミットしないこと**
（`.gitignore` で除外済み）。
詳細は [プライバシー方針](docs/.sdd/03-extraction/privacy-policy.md)。
