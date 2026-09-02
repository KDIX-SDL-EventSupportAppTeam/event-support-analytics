# 不具合レポートが届いたら

利用者が画面で受け取るのは、次の3種類のうちどれかである。

| 画面 | 出る条件 | 実装 |
|---|---|---|
| 🛑 画面を表示できませんでした | 想定外の例外で処理が止まった | `report.guarded()` |
| 📭 表示するデータがありません | 中間テーブルが読めない | `report.show_data_missing_screen()` |
| ❓ 表示がおかしいときは（サイドバー） | 利用者の自己申告。常時ある | `dashboard.main()` |

**この仕組みは当初、運営メンバー（非エンジニア）が自力で状況を伝えられるように
作られたものである。** 2026-08-31 に読み手はエンジニアへ切り替わったので、
レポートを待たずに直接 Cloud Logging を見たほうが速い場面が増えている。
それでも「レポート番号」と「そのとき選んでいた条件」は再現の手がかりとして有効なので、
仕組み自体は残している。

## どの画面に効くか

3画面すべてに効く。統合アプリでは `src/app.py` が各画面の `main` を
`report.guarded()` で包み、単体起動では各ファイルの `__main__` が同じことをする。

| 画面 | エントリ |
|---|---|
| 📊 去年の行動データ | `dashboard.main` |
| 🚦 推薦の当日監視 | `live_dashboard.main` |
| 📈 推薦の事後分析 | `post_analysis.main` |

ただし**「❓ 表示がおかしいときは」のサイドバー項目は `dashboard.py` にしかない。**
推薦の2画面で異常に気づいた場合、自己申告の導線は無いので Cloud Logging を見る。

いずれも**同じ書式のテキスト**を提示し、「コピーして担当者に送ってください」と案内する。
ファイル保存ボタン（メール添付用）も付く。

## レポートに入っているもの

```
レポート番号: A1B2C3D4      … 口頭で伝えられる短い識別子
症状:                        … どの画面から出たか
実行環境                     … 発生時刻(JST) / Python / Streamlit / OS /
                               Cloud Run のサービス名・リビジョン
そのとき選んでいた条件        … 開催日・年代・性別・ジャンル・各チェックボックス
技術的な詳細                 … スタックトレース全文（例外の場合のみ）
```

**参加者の行動データは一切含めない。** レポートはチャット等で転送されるため、
`data/` の中身が外へ出る経路にしない（`tests/test_report.py` で検証している）。

## 受け取ったあとの動き方

1. スタックトレースの最下段を読む。多くは `data/tables/*.csv` の不整合か、
   絞り込み結果が空になったことによる `KeyError` / `ValueError`
2. 「そのとき選んでいた条件」を手元で再現する

   ```bash
   streamlit run src/app.py
   ```

3. サーバー側のログも見る。トレースは stderr にも出しているので Cloud Logging に残る

```bash
gcloud run services logs read protofes-dashboard --region asia-northeast1 --project event-support-app --limit 50
```

レポート番号で対応するアクセスを絞り込むことはできない（番号はブラウザ側だけの識別子）。
時刻とリビジョンで突き合わせる。

## この仕組みで拾えない状態

アプリの Python が動いていないケースは、当然ながらアプリ自身では表示できない。

| 症状（利用者の見え方） | 実際に起きていること | 対処 |
|---|---|---|
| 画面が固まる／操作しても反応しない | WebSocket が切れた（Cloud Run のインスタンス入れ替え等） | 再読み込みで直る。**サイドバーの「❓ 表示がおかしいときは」は表示済みなのでコピーできる** |
| 「Error: Server Connection Error」 | 同上 | 同上 |
| Google のエラーページが出る（503 等） | コンテナが起動していない／落ちた | Cloud Logging を見る。`gcloud run services logs read` |
| 合言葉の画面が出ずエラーになる | Secret Manager の紐づけ漏れ | `bash deploy/deploy.sh --set-password` で再設定 |
| 当日監視の数字が更新されなくなる | メニューで別の画面へ移った | 自動更新は表示中の画面でのみ動く。当日は開いたままにする |

いずれも Cloud Logging を先に見る。

```bash
gcloud run services logs read protofes-dashboard --region asia-northeast1 --project event-support-app --limit 50
```
