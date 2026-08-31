<?php
/**
 * 読み取り専用の口の設定。**このファイルをコピーして config.php を作る。**
 *
 *   cp config.sample.php config.php   # そのうえで中身を書き換える
 *
 * config.php は鍵を含むのでリポジトリにコミットしない（.gitignore 済み）。
 */

declare(strict_types=1);

return [
    // この口の鍵。**既存の SAKURA_PROXY_KEY とは別の、新しくランダムに作った値にする。**
    //   openssl rand -hex 32
    'proxy_key' => 'ここに新しい鍵を貼る',

    // DB 接続先。さくら上では MySQL はローカルにある
    'db_host' => 'localhost',
    'db_port' => 3306,
    'db_name' => 'ここにDB名',

    // **読み取り専用ユーザー**（grant.sql で作るもの）。
    // 権限を張れなかった場合は既存のユーザーでも動くが、その場合は
    // index.php の SELECT 判定だけが書き込みを止めていることになる（README 参照）。
    'db_user' => 'bingo_ro',
    'db_pass' => 'ここに読み取り専用ユーザーのパスワード',
];
