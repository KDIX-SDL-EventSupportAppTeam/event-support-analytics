<?php
// ローカル検証用の設定。**本番の値は入れない。**
// 実際の config.php は config.sample.php をコピーして作る。
declare(strict_types=1);

return [
    'proxy_key' => 'verify-readonly-key',
    'db_host' => 'mysql',
    'db_port' => 3306,
    'db_name' => 'event_support_verify',
    'db_user' => getenv('VERIFY_DB_USER') ?: 'bingo_ro',
    'db_pass' => getenv('VERIFY_DB_PASS') ?: 'verify-ro-pass',
];
