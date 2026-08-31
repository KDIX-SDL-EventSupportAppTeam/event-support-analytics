# ダッシュボードを共有する（Cloud Run へのデプロイ）

同じ GCP プロジェクトに入っていないエンジニアへ、
[`src/app.py`](../../src/app.py) の画面を URL で共有するための手順。

> **読み手はエンジニアである。** この画面は当初、運営メンバー（非エンジニア）に
> 見てもらう前提で作られていたが、2026-08-31 にエンジニアが確認するものへ切り替わった。
> 平易な言い換えや用語解説が残っている箇所があるのは、その名残である。

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

**さくら側の読み取り専用の口がまだ用意されていない**ため、デプロイ版で動くのは
**リハーサル用の合成データだけ**である。イメージのビルド時に `synth_rec_data.py` が固定シードで生成する。

当日に実データを見るには、口が用意されたあと `REC_READONLY_PROXY_URL` /
`REC_READONLY_PROXY_KEY` を設定し、画面の取得口を `rec_db.SqlSource` へ差し替える
（[02 §4](../specs/recommendation-evaluation/02-data-source.md)）。
**書き込み可能な `SAKURA_PROXY_*` は渡さない。**

`/ops/state` と `/demo`（推薦側のパラメータ調整シミュレータ）を使うには、
推薦サービスの base URL を `RECOMMEND_BASE_URL` に、認証トークンを `RECOMMEND_OPS_TOKEN` に
設定する。`RECOMMEND_OPS_TOKEN` は推薦サービスの `OPS_TOKEN` と**同一の秘密**なので、
Secret Manager の同じシークレット（`RECOMMEND_OPS_TOKEN`）をこの Cloud Run にも
`--set-secrets` で渡す（推薦側 ADR 0008。ヘッダは `X-Ops-Token`）。
**どちらが欠けても監視自体は動く**（`/ops/state` 由来の欄だけが埋まらない）。
表示は欠け方で変わる。**当日の切り分けはこの区別で行う。**

| 状況 | `/ops/state` 欄 |
|---|---|
| 両方とも設定済み・正常 | 値が出る |
| `RECOMMEND_BASE_URL` あり・`RECOMMEND_OPS_TOKEN` が未設定か誤り | **認証エラー**（こちらの設定を直す） |
| `RECOMMEND_BASE_URL` が未設定 | **取得不能**（ローカルの `ops_state.json` を読みにいく） |
| 推薦側が `OPS_TOKEN` 未設定で `/ops/*` ごと 404 | **取得不能**（向こうの設定を直す） |
| 推薦エンジンが落ちている | **取得不能** |

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

### 3. 確認する人へ渡す

URL と合言葉を渡す。**2つは別の経路で送る**こと（例: URL は Slack、合言葉は口頭）。
受け取る側は URL を開き、合言葉を入力するだけでよい。GCP のアカウントは要らない。

### 合言葉だけを変える（メンバーが抜けたときなど）

```bash
bash deploy/deploy.sh --set-password
```

### 公開をやめる

```bash
gcloud run services delete protofes-dashboard --region asia-northeast1 --project event-support-app
```

## ローカルでの動作確認

### 統合アプリを起動する

デプロイ版と同じ3画面が出る。合言葉を設定しなければ認証なしで起動する。

```bash
streamlit run src/app.py
```

左のサイドバー上部の「画面」メニューで行き来する。

| メニュー | パス | 読むデータ |
|---|---|---|
| 📊 去年の行動データ | `/last-year`（既定） | `data/tables/*.csv` |
| 🚦 推薦の当日監視 | `/live` | `data/synth`（合成） |
| 📈 推薦の事後分析 | `/post` | `data/synth` ＋ `data/tables`（図①の去年比較） |

### 合成データを用意する

推薦の2画面が読む。無ければ先に作る（固定シードなので何度でも同じものが出る）。

```bash
python src/synth_rec_data.py --out data/synth
```

推薦エンジンが死んでいる状態も作れる。**リハーサルではこちらも必ず通すこと**
（[仕様 03](../specs/recommendation-evaluation/03-live-dashboard.md) §6）。
サイドバーのディレクトリを `data/synth_dead` に変えると、フォールバック率が 🔴 になり、
`/ops/state` の欄が「取得不能」でも他の指標が動き続けることを確認できる。

```bash
python src/synth_rec_data.py --out data/synth_dead --recommender-dead --no-ops-state
```

### 合言葉つきで確認する（デプロイ版と同じ挙動）

**合言葉を入れるまでメニュー自体が出ない**こと（＝どの画面にも入れないこと）を確認する。

```bash
DASHBOARD_PASSWORD=test1234 streamlit run src/app.py
```

PowerShell では環境変数の渡し方が違う。

```powershell
$env:DASHBOARD_PASSWORD="test1234"; streamlit run src/app.py
```

### 個別の画面だけを開く（開発用）

`src/app.py` を通さなくても単体で動く。認証・エラーレポート画面も同じように効く。

```bash
streamlit run src/dashboard.py
```

```bash
streamlit run src/live_dashboard.py
```

```bash
streamlit run src/post_analysis.py
```

### コンテナごと確認する（デプロイ直前）

イメージ内で合成データが生成され、依存も下限指定から最新へ解決される
（手元と版が変わりうるので、デプロイ前にはここまで通しておく）。

```bash
docker build -t protofes-dashboard-test .
```

```bash
docker run --rm -p 8600:8080 -e DASHBOARD_PASSWORD=test1234 protofes-dashboard-test
```

`http://localhost:8600` を開く。確認が済んだら消す。

```bash
docker rmi protofes-dashboard-test
```

> Docker Desktop の起動直後は `npipe:////./pipe/dockerDesktopLinuxEngine` が見つからず
> ビルドに失敗する。Linux エンジンが上がりきるまで待ってから再実行する。

### テスト

実データにも Firestore にも接続しない（合成データのみ）。

```bash
python -m pytest
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
読み手がエンジニアに限られた今、この移行の障壁は下がっている。

## データ上の注意

- イメージには **302名分の仮名化済み行動履歴が含まれる**。
  Artifact Registry のリポジトリを公開設定にしないこと
- 含まれないもの: メールアドレス、パスワードハッシュ、仮名IDと本人の対応表
  （[privacy-policy.md](../specs/analytics-pipeline/03-extraction/privacy-policy.md) により抽出時点で破棄済み）
- 画面には3名未満の絞り込み結果を出さない仕組み（小セル抑制）が入っている。
  誰がどう絞り込んでも、個人が特定される粒度までは表示されない
