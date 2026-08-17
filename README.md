# event-support-analytics

2025年「第3回プロトフェス」の来場者行動データを分析し、
2026年のイベント支援アプリの設計判断を根拠づけるためのリポジトリ。

## ドキュメント

| 読むもの | 内容 |
|---|---|
| [docs/PURPOSE.md](docs/PURPOSE.md) | **このリポジトリの存在意義**。最初に読む |
| [docs/.sdd/](docs/.sdd/) | 仕様書（意味単位で分割） |
| [AGENTS.md](AGENTS.md) | 作業指針・絶対規則 |

## 現在の状態

**仕様策定フェーズ。実装コードはまだ存在しない。**

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
