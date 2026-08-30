#!/usr/bin/env bash
# 合言葉つきダッシュボードを Cloud Run へデプロイする。
#
# 1サービスに3画面が入る（src/app.py が入口）。左のメニューで行き来する:
#   📊 去年の行動データ / 🚦 推薦の当日監視 / 📈 推薦の事後分析
#
# 前提: gcloud にログイン済みで、対象プロジェクトの編集権限があること。
# 使い方:
#   bash deploy/deploy.sh                 # 既存の合言葉のままデプロイし直す
#   bash deploy/deploy.sh --set-password  # 合言葉を新しく設定してからデプロイする
#
# 合言葉は Secret Manager に置き、環境変数として Cloud Run に注入する。
# リポジトリにも、このスクリプトにも、コマンド履歴にも平文を残さない。
set -euo pipefail

# デプロイ先。データの抽出元（protofes）とは別のプロジェクトでよい。
# この画面は Firestore に接続せず、焼き込んだ CSV だけを読むため。
PROJECT="${PROJECT:-event-support-app}"
REGION="${REGION:-asia-northeast1}"
SERVICE="${SERVICE:-protofes-dashboard}"
SECRET="${SECRET:-dashboard-password}"
# 不具合レポートの送り先として画面に出す文言（連絡先を入れておくと親切）
SUPPORT_CONTACT="${SUPPORT_CONTACT:-この画面を共有した担当者}"

cd "$(dirname "$0")/.."

if [[ ! -f data/tables/participants.csv ]]; then
  echo "data/tables/ に中間テーブルがありません。先に python src/run_pipeline.py を実行してください。" >&2
  exit 1
fi

echo "== プロジェクト: ${PROJECT} / リージョン: ${REGION} / サービス: ${SERVICE}"
gcloud config set project "${PROJECT}" >/dev/null

gcloud services enable run.googleapis.com secretmanager.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com --project "${PROJECT}"

# --- 合言葉 -------------------------------------------------------------------
if ! gcloud secrets describe "${SECRET}" --project "${PROJECT}" >/dev/null 2>&1; then
  gcloud secrets create "${SECRET}" --replication-policy=automatic --project "${PROJECT}"
  NEED_PASSWORD=1
fi

if [[ "${1:-}" == "--set-password" || "${NEED_PASSWORD:-0}" == "1" ]]; then
  echo "共有する合言葉を入力してください（画面には表示されません）:"
  read -r -s NEW_PASSWORD
  printf '%s' "${NEW_PASSWORD}" | gcloud secrets versions add "${SECRET}" --data-file=- --project "${PROJECT}"
  unset NEW_PASSWORD
  echo "合言葉を保存しました。"
fi

# Cloud Run のランタイム SA にシークレットの読み取りを許可する
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)')"
gcloud secrets add-iam-policy-binding "${SECRET}" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor \
  --project "${PROJECT}" >/dev/null

# --- デプロイ -----------------------------------------------------------------
# --allow-unauthenticated は「GCP の認証を求めない」という意味であり、
# アクセス制御は画面側の合言葉（src/auth.py）が担う。
gcloud run deploy "${SERVICE}" \
  --source . \
  --project "${PROJECT}" \
  --region "${REGION}" \
  --allow-unauthenticated \
  --port 8080 \
  --memory 1Gi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 3 \
  --session-affinity \
  --set-secrets "DASHBOARD_PASSWORD=${SECRET}:latest" \
  --set-env-vars "DASHBOARD_AUTH_REQUIRED=1,SUPPORT_CONTACT=${SUPPORT_CONTACT}"

echo
echo "URL: $(gcloud run services describe "${SERVICE}" --region "${REGION}" --project "${PROJECT}" --format='value(status.url)')"
echo "この URL と合言葉を運営メンバーへ共有してください（別々の経路で渡すこと）。"
