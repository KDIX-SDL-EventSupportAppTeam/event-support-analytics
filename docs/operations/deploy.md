# ダッシュボードを共有する（Cloud Run へのデプロイ）

同じ GCP プロジェクトに入っていない運営メンバーへ、
[`src/app.py`](../../src/app.py) の画面を URL で共有するための手順。

**アクセス制御は「共有の合言葉」1つのみ**である。強度と限界は末尾に書く。

## 構成

**1つの URL に3画面が入っている。** 左のメニューで行き来する。

```
ブラウザ ──► Cloud Run（誰でも到達できる URL）
               └─ 合言葉の入力画面（src/auth.py）
                    └─ 合言葉が一致 ─► 統合アプリ（src/app.py）
                                         ├─ 📊 去年の行動データ   /last-year（既定）
                                         ├─ 🚦 推薦の当日監視     /live
                                         └─ 📈 推薦の事後分析     /post
             合言葉は Secret Manager から環境変数として注入
             データ（data/tables/*.csv）はイメージに焼き込み済み
             推薦の2画面が読む合成データはイメージのビルド時に生成
```

**合言葉は入口（`src/app.py`）で1回だけ確認する。** 認証を通るまでメニューを組み立てない
ので、どの画面にも入れない。画面を追加したときに認証を付け忘れる事故を構造で防ぐため、
個別画面ではなく入口で止めている。

### 推薦の2画面について

**本番 MySQL への接続経路が未確定**（[仕様 E-1](../specs/recommendation-evaluation/02-data-source.md) §4）
のため、デプロイ版で動くのは**リハーサル用の合成データだけ**である。
イメージのビルド時に `synth_rec_data.py` が固定シードで生成する。

当日に実データを見るには、経路が決まったあと `rec_db.SqlSource` の実装が要る。
それまでは「画面の作りと導線を運営に見てもらう」用途にとどまる。

当日監視の画面は45秒ごとに自動更新される。**メニューで別の画面へ移ると更新は止まる。**
当日はこの画面を開いたままにしておくこと。

Firestore へは接続しない。コンテナが読むのは焼き込まれた CSV だけである
（「Firestore へのアクセスは抽出時の一度きり」という AGENTS.md の規則を守るため）。

**デプロイ先は `event-support-app`（今年のチームのプロジェクト）。**
データの抽出元である `protofes` は先輩世代のプロジェクトであり、
新しいリソースも課金も足さない。接続しない以上、置き場所は分けてよい。

## 手順

### 1. データを用意する

```bash
python src/run_pipeline.py
```

`data/tables/` に3つの CSV ができていること。これがイメージに入る。

### 2. デプロイする

```bash
bash deploy/deploy.sh --set-password
```

合言葉の入力を求められる（画面には表示されない）。入力した値は Secret Manager に保存され、
リポジトリにもコマンド履歴にも残らない。完了すると URL が表示される。

2回目以降、合言葉を変えないなら `--set-password` は不要:

```bash
bash deploy/deploy.sh
```

プロジェクトやサービス名を変えたいときは環境変数で上書きする:

```bash
PROJECT=event-support-app REGION=asia-northeast1 SERVICE=protofes-dashboard bash deploy/deploy.sh
```

### 3. 運営メンバーへ渡す

URL と合言葉を渡す。**2つは別の経路で送る**こと（例: URL は Slack、合言葉は口頭）。
メンバー側は URL を開き、合言葉を入力するだけでよい。GCP のアカウントは要らない。

### 合言葉だけを変える（メンバーが抜けたときなど）

```bash
bash deploy/deploy.sh --set-password
```

### 公開をやめる

```bash
gcloud run services delete protofes-dashboard --region asia-northeast1 --project event-support-app
```

## ローカルでの動作確認

合言葉を設定しなければ、これまで通り認証なしで起動する。
デプロイ版と同じ3画面を出すには統合アプリを起動する。

```bash
streamlit run src/app.py
```

推薦の2画面は合成データを読む。無ければ先に作る。

```bash
python src/synth_rec_data.py --out data/synth
```

合言葉つきの画面を手元で確認したいとき:

```bash
DASHBOARD_PASSWORD=test1234 streamlit run src/app.py
```

個別の画面だけを開きたいとき（開発用。`src/app.py` を通さなくても動く）:

```bash
streamlit run src/live_dashboard.py
```

コンテナごと確認したいとき:

```bash
docker build -t dashboard . && docker run -p 8080:8080 -e DASHBOARD_PASSWORD=test1234 dashboard
```

## 不具合が起きたときの連絡先を画面に出す

画面が止まったときに表示される「担当者に送ってください」の宛先を指定できる。

```bash
SUPPORT_CONTACT="開発担当（Slack: @akihide）" bash deploy/deploy.sh
```

指定しない場合は「この画面を共有した担当者」と表示される。
利用者が送ってくる内容については [TROUBLESHOOTING.md](troubleshooting.md) を参照。

## この認証の強度と限界

**想定している脅威は「URL がたまたま知られて中身を見られる」ことまで。**
身内向けの共有としては十分だが、次の点は理解した上で使うこと。

- 合言葉は全員で共通。**誰が見たかは記録されない**
- 総当たりへの対策は「1回失敗ごとに1秒待つ」「同一セッションで10回失敗したら閉じる」のみ。
  ブラウザを開き直せばリセットされる
- 合言葉が漏れれば、URL を知る誰でも見られる。漏れた疑いがあれば即座に変更する
- Cloud Run 自体は誰でも到達できる（`--allow-unauthenticated`）。
  防いでいるのはアプリ側だけである

**もっと強い制御が要るなら**、Identity-Aware Proxy（IAP）＋ Google アカウントでの
許可リストに切り替える。「誰が見たか」がログに残り、退職・離脱時の遮断も個別にできる。
非エンジニアでも Google アカウントさえあれば使える。

## データ上の注意

- イメージには **302名分の仮名化済み行動履歴が含まれる**。
  Artifact Registry のリポジトリを公開設定にしないこと
- 含まれないもの: メールアドレス、パスワードハッシュ、仮名IDと本人の対応表
  （[privacy-policy.md](../specs/analytics-pipeline/03-extraction/privacy-policy.md) により抽出時点で破棄済み）
- 画面には3名未満の絞り込み結果を出さない仕組み（小セル抑制）が入っている。
  誰がどう絞り込んでも、個人が特定される粒度までは表示されない
