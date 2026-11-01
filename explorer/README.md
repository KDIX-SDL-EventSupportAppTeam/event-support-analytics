# Event Data Explorer（事後分析用・ローカル専用）

イベントの元データを**加工せず**、ID のつながりでノード表示するビューア。
既存の Streamlit ダッシュボード（`src/`）とは独立している。

## データ

完全ダンプ: `../db-archive/2026-10-16-prod-event/full/`（`tables/*.parquet` 1 テーブル 1 ファイル + `manifest.json`）。
`fetch_full_dump.py` で読み取り専用プロキシから取得。値は全列 string で DB の保存値のまま、認証情報の列（password / token / email）は取らない。
**個人情報を含む。ディレクトリは 700・ファイルは 600。中身を標準出力・チャット・スクリーンショットに出さないこと。**

```bash
SSL_CERT_FILE="$(.venv/bin/python -c 'import certifi;print(certifi.where())')" \
  .venv/bin/python explorer/fetch_full_dump.py ../db-archive/2026-10-16-prod-event/full 52b98b06-8729-496d-b46a-f54e81363bd3
```

## スプレッドシート出力

```bash
SSL_CERT_FILE="$(.venv/bin/python -c 'import certifi;print(certifi.where())')" \
  .venv/bin/python explorer/fetch_full_dump.py ../db-archive/2026-10-16-prod-event/full 52b98b06-8729-496d-b46a-f54e81363bd3 --with-email   # メール列込みの行を sensitive/ に別保存
.venv/bin/python explorer/export_xlsx.py ../db-archive/2026-10-16-prod-event/full            # → full/export/*.xlsx
.venv/bin/python explorer/export_xlsx.py ../db-archive/2026-10-16-prod-event/full --encrypt  # パスワード付き（端末で入力）
```

- README / 列定義 / テーブルごとのシート。全セル文字列、NULL は空セル。書き出し後に全セルを読み戻して照合
- メールは users / organizers / exhibitor_invites のシートにだけ入る（explorer は sensitive/ を読まない）
- 必要パッケージ: openpyxl, msoffcrypto-tool（--encrypt 時）

### 非エンジニア向けまとめ

```bash
.venv/bin/python explorer/export_readable_xlsx.py ../db-archive/2026-10-16-prod-event/full   # → full/export/イベントまとめ_非エンジニア向け_*.xlsx
```

ID を名前・ブース名・日本語に置き換え、時刻を日本時間に統一（UTC 列だけ +9h）。概要 → 参加者 → 行動の流れ → ブース → コメント → アワード → おすすめ → ガチャ → ビンゴ → アンケート → 時間帯 → 用語 の 14 シート。

### 研究データ（エンジニア・研究者向け）

```bash
.venv/bin/python explorer/export_research_xlsx.py ../db-archive/2026-10-16-prod-event/full   # → full/export/推薦評価_研究データ_*.xlsx
```

role=participant のみ・仮名 ID（対応表なし）・JST 統一。推薦枠（1枠1行：戦略・arm・期間・interest_match・訪問・30分以内・★4・セレンディピティ・観察時間・共変量）/ 候補スコア全件 / 解放イベント / チェックイン / 参加者 / ブース / 集計（ファネル・ABA・決定表件数帯）/ 参加者内比較 / 規則の発火 / 周遊 ECDF / 列定義。定義は recommend 09-research-design §5 の事前登録どおり。主要母集団の件数は ANALYSIS.md と一致（RANDOM 278 / DRSA 881 / SER 117 / EXPLORE 198）。

## 起動

```bash
.venv/bin/python explorer/export_data.py ../db-archive/2026-10-16-prod-event/full   # ダンプ → explorer/data/*.json
python3 -m http.server 8510 -d explorer                                  # http://localhost:8510
```

`explorer/data/` は `.gitignore` 対象（個人の行動履歴を含む）。

### SQL 実験室

```bash
.venv/bin/streamlit run explorer/sql_lab.py --server.port 8511 -- ../db-archive/2026-10-16-prod-event/full
```

ノード表示の左上「ノード表示 / SQL 実験室」で切り替える（`http://localhost:8510/#sql` で直接開ける）。SQL 実験室は iframe で埋め込んでいるので、切り替えてもキャンバスと SQL の状態はそれぞれ残る。

- DuckDB で `full/tables/*.parquet` に SQL を投げる。`check_ins` などテーブル名そのままは manifest の db_type で型付け済み（int/datetime/json）、`raw.check_ins` は保存値そのまま（全列 string）
- JSON 列は `reason_payload->>'strategy'` で取り出せる。時刻は補正しない（JST/UTC 混在）
- 結果は表（並べ替え・検索）／グラフ（棒・折れ線・散布図・ヒストグラム・箱ひげ）／CSV ダウンロード
- よく使う SQL は「クエリを保存」で `explorer/queries/*.sql` に残し、左のメニューから読み込む
- 左の「アンケート取り込み」: イベント後アンケートのスプレッドシートを見出し行ごとコピーして貼り付け → メアドで `sensitive/users` と照合し、紐付いた人だけ `full/survey/post_survey.parquet`（1 人 1 行・user_id キー・メアドは保存しない）に追記。紐付かないメアド・メアドでない値は画面に一覧を出して除外。保存済みの人は追加せず、新規がなければファイルを変えない。同じ人が複数行なら最後の行。質問は見出しの文で `q1..` に対応付け（`post_survey_questions`）、新しい質問は番号を足す
- SQL では `post_survey`（横持ち）/ `post_survey_long`（1 回答 1 行、`answer_num` は数値化）で、`user_id` から check_ins・bingo_cards などと結合できる。同じページの「メアドから引く」で 1 人分の行動と回答を確認できる
- 左の「研究分析」（research.py）: アンケート q1〜q4 × 行動データ。0 概要・回答の偏り / A 自己申告と実際（q1・q4）/ B カード内外の訪問（q1）/ C 推薦方式（1 人ごとの戦略の内訳 × q2・q3、枠単位の訪問・★4・セレンディピティ）/ D 補足（phase・年代・ビンゴ・ガチャ）/ 個人別の表。定義は推薦評価_研究データと同じ。選択肢の並び順は画面で並べ替えて `full/survey/answer_order.json` に保存
- 例外として、メアド照合のためにアンケート取り込みだけが sensitive/users を読む。それ以外は sensitive/ を読まない。CSV には個人の行動履歴が入りうるので、full/ と同じ扱いにする

## 使い方

- 左で「何を見る？（ユーザー/ブース/アワード/カテゴリ）」→ 絞り込み → 並び順（ランダム可・🎲で引き直し）→ 個数 →「キャンバスに追加」
- ノードをクリック → メニュー（ユーザーなら 訪問したブース／ビンゴカード／アワード投票先／ガチャコイン／アンケート回答／推薦されたブース／カード解放の履歴）
- メニューをクリック → 中身が広がる。もう一度で閉じる。多い場合は「…残り n 件」で追加表示（上限はツールバー）
- 線の文字は「何番目・時刻・★」など。線をクリックすると、その根拠になった元データの行が右に出る
- 削除は、そのノードから広げたものも全部消す（他のノードからも繋がっているユーザー/ブースは残す）
- 「似ているものでまとめる」: キャンバス上のユーザー/ブースを属性ベクトル（アンケート回答・訪問カテゴリ／来場者構成・評価・訪問数）で k-means。グループの特徴を枠の見出しに表示
- 右パネル「数字で見る」: 対象（左の絞り込み結果／キャンバス上／選択中）について、人数・チェックイン・評価・推薦枠と推薦先の訪問・投票・ガチャ・ビンゴなどを数える。棒グラフの行をクリックするとその条件で絞り込む
- 絞り込みの「条件」: 推薦を受けた／推薦先を訪問した／コメントした／ガチャを使った など。並び順は各指標の多い順・少ない順
- 「推薦先を訪問」は同じユーザーが推薦ブースにチェックインしているか（時刻は JST/UTC 混在のため比較しない）
- ③ で「🎲 ランダムに」か「条件の順に上から」を選び、個数ぶん表示（ユーザーは初期状態で role=participant に絞り込み）
- ↶ 戻す / ↷ やり直す（⌘Z / ⌘⇧Z）。展開・削除・整理・追加はすべて戻せる
- 展開時は重ならない位置へ自動で押し出す。「整理…」で 重なり解消 / つながり / 階層 / 同心円 / 種類ごと / 格子 / 全部閉じる / 選択だけ残す / 値ノードを消す
- ◆ イベント: イベント全体のノード。メニューに数字・役割別ユーザー・カテゴリ別ブース・アワード・質問・設定。詳細パネルに参加者とブースのサマリ
- 左パネル（リモコン）はスクロールでき、絞り込みはチップで複数選択（同じ項目内は「どれか」、「条件」は「すべて満たす」）。設定してから下端の「この条件で表示（反映）」で反映、「追加」で今のキャンバスに足す
- 整列（L）と「自動整列」: 起点→メニュー→中身の親子で扇を割り当てる放射ツリー。子孫の数に比例した角度＋扇の両端の余白で、親が違う孫同士は離れる。起点ごとの円は重ならないよう棚詰め
- ホバー: 起点までの親（橙）と子（緑）を強調し、ポップアップで親との関係を表示。右パネル上部にも全文（ユーザー×ブースなら 訪問・評価・コメント・アワード投票・推薦・ビンゴ の有無と中身）
- 操作: ドラッグ=範囲選択、2本指スワイプ=移動、ピンチ=拡大縮小、Space+ドラッグ=移動、Delete/L/F/Esc


時刻は DB の保存値そのまま（列によって JST/UTC が混在。補正はしない）。
