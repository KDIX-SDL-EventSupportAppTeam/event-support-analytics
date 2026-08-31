# さくらDB プロキシ — 読み取り専用の口（設置手順）

**先生へのお願いは、この1ページで完結します。作業は3つ、5分ほどです。**

分析側（`event-support-analytics` / `event-support-recommend`）が本番 DB を**読む**ための口です。
既存の `/bingo/query/index.php` とは別物で、**SELECT しか通りません**。

なぜ別に作るか: 既存の口の鍵は全権で、分析側から `UPDATE` や `DROP` が通ってしまいます。
分析に必要なのは読むことだけなので、権限の側で落としておきたい、という理由です。

**この一式は、こちらで MySQL 8 と PHP 8 を立てて実際に通してあります**（33項目すべて成功）。
検証の中身は [verify/verify.py](verify/verify.py) にあり、いつでも再実行できます。

---

## お願いする作業（3つ）

### 1. 読み取り専用ユーザーを作る

phpMyAdmin の「SQL」タブに [grant.sql](grant.sql) を貼り、**`<DB名>` と `<パスワード>` を
置き換えて**実行してください。パスワードは適当な長い文字列で構いません。

> **もし権限を追加できなかったら、それでも進められます。** その場合は 2 の `config.php` に
> **既存のDBユーザー**を書いてください。書き込みは `index.php` 側の SELECT 判定が止めます
> （壁が2枚から1枚に減るだけで、動作は変わりません）。**この件で止まらないでください。**

### 2. PHP を置く

既存の口とは別のディレクトリに、2ファイル置いてください。

```
~/www/bingo/query-ro/
    index.php      ← このディレクトリの index.php をそのまま
    config.php     ← config.sample.php をコピーして中身を書き換える
```

`config.php` に入れるもの: **この口専用の新しい鍵**（`openssl rand -hex 32` など）、DB名、
1で作ったユーザーとパスワード。**既存の `SAKURA_PROXY_KEY` は使い回さないでください。**

### 3. 動いたことを確認して、URL と鍵を返す

```bash
# 鍵なし → 401 が返れば正常
curl -s -X POST https://sutolab.sakura.ne.jp/bingo/query-ro/index.php \
  -H "Content-Type: application/json" -d '{"sql":"SELECT 1","params":[]}'

# 鍵あり → {"rows":[{"ok":1}],"affectedRows":0,"insertId":null}
curl -s -X POST https://sutolab.sakura.ne.jp/bingo/query-ro/index.php \
  -H "Content-Type: application/json" -H "X-Proxy-Key: <新しい鍵>" \
  -d '{"sql":"SELECT 1 AS ok","params":[]}'

# 書き込み → {"error":"Forbidden: read-only endpoint"} が返れば正常
curl -s -X POST https://sutolab.sakura.ne.jp/bingo/query-ro/index.php \
  -H "Content-Type: application/json" -H "X-Proxy-Key: <新しい鍵>" \
  -d '{"sql":"UPDATE users SET role = 1","params":[]}'
```

3つとも想定どおりなら完了です。**URL と鍵を、鍵は安全な経路で**お知らせください。
こちらは環境変数に入れるだけで、その日から本番を読めます。

---

## 中身（レビュー用）

| ファイル | 何か |
|---|---|
| [index.php](index.php) | 口の本体。認証 → SELECT 判定 → PDO で実行 → JSON |
| [config.sample.php](config.sample.php) | 設定の雛形。鍵と DB 接続情報 |
| [grant.sql](grant.sql) | 読み取り専用ユーザーの作成 |
| [verify/](verify/) | Docker で MySQL と PHP を立てて通しきる検証一式 |

契約（リクエスト・レスポンスの形）は**既存の口とまったく同じ**です。正本は
`event-support-server/src/db/http-proxy.ts`。エラーを 500 に潰す挙動も既存に合わせてあります。

安全側の作りは3つです。

1. **MySQL の権限** — SELECT のみ。`users` は `id` と `role` だけに列を絞る
   （`email` / `password_hash` は権限の側で読めない）
2. **`index.php` の SELECT 判定** — SELECT 1文でなければ 403。複文・コメント偽装も拒む
3. **呼ぶ側**（`src/rec_db.py` の `SqlSource`）— テーブル名と列を定義から組み立て、
   `SELECT *` を書かない

---

## 検証を自分で回す

```bash
python deploy/sakura-readonly-proxy/verify/verify.py
```

Docker が要ります。使い捨ての MySQL 8 に `event-support-server/db/create-tables.sql`
（スキーマの正本）をそのまま流し、`grant.sql` の文で読み取り専用ユーザーを作り、
`index.php` を PHP 8 で動かして、`rec_db.SqlSource` から実際に読みます。
**本番のさくらには一切触りません。** 終わるとコンテナは消えます。

見ているもの: 契約どおりの応答／401・404／書き込みが PHP と権限の両方で止まること／
`users.email` が読めないこと／`LIVE_TABLES`・`POST_TABLES` の全テーブルが読めること／
`created_at` が UTC の datetime になること／`card_id` → `user_id` の解決が通ること。

> この検証で実際にバグを1つ潰しました。SELECT 判定の正規表現が `created_at` の中の
> `create` に反応し、正当な SELECT を弾いていました。**先生の環境で踏む前に見つけるのが、
> この一式の目的です。**
