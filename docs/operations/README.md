# 運用手順

| ファイル | 内容 |
|---|---|
| [deploy.md](deploy.md) | Cloud Run への公開と共有手順・**実行コマンド一式** |
| [troubleshooting.md](troubleshooting.md) | 停止時の不具合レポートと対応 |

## よく使うコマンド

```bash
streamlit run src/app.py
```

3画面（去年の行動データ／推薦の当日監視／推薦の事後分析）が1つのアプリに入っている。
左のメニューで行き来する。推薦の2画面には合成データが要る。

```bash
python src/synth_rec_data.py --out data/synth
```

各コマンドの意味と、合言葉つき・コンテナでの確認方法は [deploy.md](deploy.md#ローカルでの動作確認) を参照。
