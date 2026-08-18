# Cloud Run 用イメージ。
#
# data/tables/*.csv をイメージに焼き込む。Cloud Run はディスクを持たないため、
# Firestore へ再接続せずに動かすにはこれが最も単純である（抽出は一度きり、という
# AGENTS.md の規則にも合う）。イメージには仮名化済みの行動履歴が含まれるので、
# Artifact Registry のリポジトリは公開しないこと。
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DASHBOARD_AUTH_REQUIRED=1

WORKDIR /app

# 分析専用の依存だけを入れる（Firestore SDK と pytest はデプロイに不要）
COPY requirements.txt ./
RUN pip install --no-cache-dir \
      "numpy>=2.0" "pandas>=2.2.2" "plotly>=5.24" "streamlit>=1.40"

COPY src/ ./src/
COPY data/tables/ ./data/tables/

# Cloud Run は $PORT で待ち受けることを要求する
ENV PORT=8080
EXPOSE 8080

CMD streamlit run src/dashboard.py \
    --server.port=${PORT} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=true \
    --browser.gatherUsageStats=false
