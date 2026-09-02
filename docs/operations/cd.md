# main への push で自動デプロイする（CD）

[`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml) が、`main` への push で
Cloud Run のサービスを更新する。手元の [`deploy/deploy.sh`](../../deploy/deploy.sh) と
同じサービス・同じ設定を使う。手動デプロイは今後も使える（両方が同じ場所を更新する）。

手動との違いは2点だけ。

1. **合言葉は CI では触らない。** Secret Manager の既存バージョン（`:latest`）を参照するだけ。
   合言葉を変えるときは今まで通り `bash deploy/deploy.sh --set-password` を手元で回す
2. **`data/tables/*.csv` は非公開の GCS バケットから取る。** `data/` は `.gitignore` 済みで
   リポジトリに無いため。**CI は Firestore へ接続しない**（「抽出は一度きり」の規則を守る）

```
git push origin main
   └─ GitHub Actions
        ├─ WIF で GCP へ認証（鍵ファイルを置かない）
        ├─ gs://<bucket>/tables/*.csv → data/tables/   ← イメージに焼く分
        └─ gcloud run deploy --source .（Cloud Build がイメージを作る）
```

## 初期設定

一度だけ行う。`PROJECT`・`REGION`・`SERVICE` は `deploy/deploy.sh` の既定値に合わせている。

```bash
PROJECT=event-support-app
REGION=asia-northeast1
BUCKET=event-support-analytics-tables
REPO=KDIX-SDL-EventSupportAppTeam/event-support-analytics
```

### 1. 中間テーブルの置き場を作り、CSV を上げる

**このバケットは公開しない。** 仮名化済みとはいえ302名分の行動履歴である。

```bash
gcloud storage buckets create "gs://${BUCKET}" --project "${PROJECT}" --location "${REGION}" --uniform-bucket-level-access
```

```bash
python src/run_pipeline.py
```

```bash
gcloud storage cp data/tables/*.csv "gs://${BUCKET}/tables/" --project "${PROJECT}"
```

以後、CSV を作り直したときだけ上のコピーをやり直す。

### 2. デプロイ用のサービスアカウントを作る

```bash
gcloud iam service-accounts create github-deployer --display-name "GitHub Actions deployer" --project "${PROJECT}"
```

```bash
SA="github-deployer@${PROJECT}.iam.gserviceaccount.com"
for ROLE in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.admin roles/storage.admin roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding "${PROJECT}" --member "serviceAccount:${SA}" --role "${ROLE}"
done
```

### 3. Workload Identity 連携を作る（鍵ファイルを作らない）

```bash
gcloud iam workload-identity-pools create github --location global --display-name "GitHub Actions" --project "${PROJECT}"
```

```bash
gcloud iam workload-identity-pools providers create-oidc github-oidc \
  --location global --workload-identity-pool github \
  --issuer-uri "https://token.actions.githubusercontent.com" \
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition "assertion.repository=='${REPO}'" \
  --project "${PROJECT}"
```

このリポジトリからのトークンだけがこの SA になれる、という結び付けを足す。

```bash
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)')"
gcloud iam service-accounts add-iam-policy-binding "${SA}" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/${REPO}" \
  --project "${PROJECT}"
```

プロバイダのフルネーム（次の手順で使う）を控える。

```bash
gcloud iam workload-identity-pools providers describe github-oidc --location global --workload-identity-pool github --format='value(name)' --project "${PROJECT}"
```

### 4. GitHub 側に変数を登録する

**Secrets ではなく Variables**（Settings → Secrets and variables → Actions → Variables）。
秘密は1つも入らない。合言葉は Secret Manager にあり、GCP への認証は WIF が担うため。

| 変数 | 値の例 |
|---|---|
| `GCP_PROJECT` | `event-support-app` |
| `GCP_REGION` | `asia-northeast1` |
| `CLOUD_RUN_SERVICE` | `protofes-dashboard` |
| `DASHBOARD_SECRET` | `dashboard-password` |
| `TABLES_GCS_URI` | `gs://event-support-analytics-tables/tables` |
| `GCP_WIF_PROVIDER` | 手順3で控えたフルネーム |
| `GCP_DEPLOY_SA` | `github-deployer@event-support-app.iam.gserviceaccount.com` |
| `SUPPORT_CONTACT` | `開発担当（Slack: @akihide）`（任意） |

ワークフローは `environment: production` を使う。GitHub の Settings → Environments で
`production` を作り、必要なら承認者を設定する（承認するまでデプロイが止まる）。

### 5. 動作を確認する

`main` へ push する前に、Actions タブから **Run workflow**（`workflow_dispatch`）で手動実行できる。
成功するとサマリに URL が出る。

## 運転上の注意

- **`develop` では動かない。** `main` への push と手動実行だけである
  （`develop` → `main` の PR は明示の指示があるときだけ作る、という [git の規則](../rules/git.md) は変えていない）
- 合言葉を変えたあとは、Cloud Run が `:latest` を読み直すよう**再デプロイが要る**
  （`deploy.sh --set-password` は再デプロイまで行う）
- 推薦の2画面はデプロイ版では**合成データのまま**である。実データへの切り替えは
  [deploy.md](deploy.md) の通り環境変数の追加が要る。CD は現状その変数を渡していない

## 課金について

`main` に push するたびに **Cloud Build が動き、イメージが Artifact Registry に積まれる**。
無料枠に収まる規模だが、放っておくとイメージの保管だけが増え続ける。古い版を消す掃除を入れておく。

```bash
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy \
  --location "${REGION}" --project "${PROJECT}" \
  --policy=<(echo '[{"name":"keep-recent","action":{"type":"Keep"},"mostRecentVersions":{"keepCount":5}},{"name":"delete-old","action":{"type":"Delete"},"condition":{"olderThan":"30d"}}]')
```

| 何に | どう課金されるか |
|---|---|
| Cloud Run | リクエスト処理中の時間だけ。`--min-instances 0` なので誰も見ていない間は 0 円 |
| Cloud Build | ビルド時間。1回あたり数分、無料枠（月あたり数千分）に収まる |
| Artifact Registry | イメージの保管量。1回のビルドで約1GB積まれるので**掃除を入れないとここだけ増える** |
| Cloud Storage | CSV 数MB。ごく僅か |
| Secret Manager / WIF | アクセス回数のみ。実質 0 円 |

**無料枠を超えるとしたら Artifact Registry の保管**である。上の掃除ポリシーを入れておけば、
このリポジトリの使い方（数人が見るダッシュボード）で月あたりの支払いが出ることはまず無い。
心配なら GCP コンソールの「お支払い」→「予算とアラート」で、このプロジェクトに
少額（例: 500円）の予算アラートを1つ置いておくと、想定外の課金にすぐ気づける。
