# データの扱い（絶対規則）

## 個人データ

- **メールアドレスとパスワードハッシュを保存しない。**
  抽出時に仮名 ID（`u0001` 形式）へ置換する。**対応表は生成しない**
- **`data/` をコミットしない。** 302名分の行動履歴はバージョン管理下に置かない
- 集計済みの結果（`output/`）はコミットしてよい
- 該当者が3名未満になる絞り込みでは集計を表示しない（属性の組み合わせから個人が推定されうる）

詳細: [privacy-policy.md](../specs/analytics-pipeline/03-extraction/privacy-policy.md)

## GCP へのアクセス

- **Firestore へのアクセスは抽出時の一度だけ。** 以降はローカルファイルのみで作業する
- **読み取り専用。** 書き込み API を呼ぶコードを書かない
- 接続先は `protofes` プロジェクトの `(default)` データベース
  （`protofest-test1` / `test2` はイベント後のクローンであり本番ではない）

詳細: [database-inventory.md](../specs/analytics-pipeline/02-data-source/database-inventory.md)

## 先輩世代のリポジトリ

`HidetsuguSuto/2025_P3_supporters_game` は先輩世代の資産である。
push 権限はあるが、**読み取り専用として扱い、一切変更しない。**

## 指標の実装

**算出式を `dashboard.py` や `visualize.py` に書かない。**
必ず `metrics.py` に置き、GUI・図表の双方から呼ぶ。仕様の二重管理を避けるため。
