# 読み取り専用プロキシ（GCP 版）

推薦エンジン（`event-support-recommend`）と分析ダッシュボードが本番DBを**読む**ための口。
さくらに PHP を置く案（[../sakura-readonly-proxy/](../sakura-readonly-proxy/)）の代替で、**さくら側には何も置かない**。

```
推薦 / 分析 ──(読み取り専用の鍵)──▶ Cloud Run: event-support-readonly-proxy ──(全権の鍵)──▶ さくら既存プロキシ /bingo/query/index.php
```

- 契約は既存の口と同じ（`POST {"sql","params"}` → `{"rows","affectedRows":0,"insertId":null}`）
- 通す SQL は `SELECT \`列\`, … FROM \`表\` [WHERE \`event_id\` = ?]` の形だけ。表と列は [grant.sql](../sakura-readonly-proxy/grant.sql) と同じ範囲（users は id / role のみ）
- 全権の鍵（`SAKURA_PROXY_KEY`）はこのサービスの中にだけ置く。呼び出し側には `RECOMMEND_READONLY_PROXY_KEY` だけを配る
- 上流が 5xx を返したら1回だけ再試行する（SELECT のみなので副作用なし）

## テスト

```bash
python3 -I test_main.py
```

## デプロイ（手動。CD は無い）

```bash
gcloud run deploy event-support-readonly-proxy --source . --region asia-northeast1 --allow-unauthenticated --min-instances 1 --max-instances 2 --memory 256Mi \
  --set-env-vars UPSTREAM_PROXY_URL=https://sutolab.sakura.ne.jp/bingo/query/index.php \
  --set-secrets READONLY_PROXY_KEY=RECOMMEND_READONLY_PROXY_KEY:latest,UPSTREAM_PROXY_KEY=SAKURA_PROXY_KEY:latest
```

`--min-instances 1` は、0 だと5分おきのスナップショットのたびにコールドスタート（約9秒）が乗り、
推薦エンジン側の1本20秒のタイムアウトに近づくため（2026-10-12 実測で取得全体 24 秒）。

実行サービスアカウントには `RECOMMEND_READONLY_PROXY_KEY` の `roles/secretmanager.secretAccessor` が要る。
