# Cloud Run 用イメージ。3画面の統合アプリ（src/app.py）を1サービスとして出す。
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
      "numpy>=2.0" "pandas>=2.2.2" "plotly>=5.24" "streamlit>=1.49"

COPY src/ ./src/
COPY data/tables/ ./data/tables/

# 推薦の当日監視・事後分析の画面が読む合成データを、イメージの中で作る。
# 本番 MySQL への接続経路が未確定なため（仕様 E-1）、デプロイ版で動かせるのは
# 今のところリハーサル用の合成データだけである。シードは固定なので毎回同じものが出る。
# data/synth は .gitignore 済みで、ビルド元の有無に依存させたくないのでここで生成する。
RUN python src/synth_rec_data.py --out data/synth
ENV REC_DATA_DIR=data/synth

# Cloud Run は $PORT で待ち受けることを要求する
ENV PORT=8080
EXPOSE 8080

# 統合アプリ。左のメニューで3画面（去年の行動データ／当日監視／事後分析）を行き来する。
# 合言葉の確認は src/app.py が入口で1回だけ行う
CMD streamlit run src/app.py \
    --server.port=${PORT} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=true \
    --browser.gatherUsageStats=false
