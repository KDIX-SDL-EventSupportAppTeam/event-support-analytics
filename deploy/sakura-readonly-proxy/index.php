<?php
/**
 * さくらDB プロキシ — 読み取り専用の口
 *
 * 既存の /bingo/query/index.php（書き込みも通る全権の口）とは別に置く、
 * SELECT しか通らない口。分析・推薦リポジトリにはこちらの鍵だけを配る。
 *
 * 決定: event-support-analytics ADR 0001（案A′）
 * 契約: 既存の口とまったく同じ。event-support-server/src/db/http-proxy.ts が正本。
 *
 *   POST <このファイルの URL>
 *     headers: Content-Type: application/json / X-Proxy-Key: <鍵>
 *     body:    {"sql": "SELECT ...", "params": [...]}
 *     → 200:   {"rows": [...], "affectedRows": 0, "insertId": null}
 *     → 401:   {"error": "Unauthorized"}
 *     → 400:   {"error": "Bad Request: ..."}
 *     → 403:   {"error": "Forbidden: read-only endpoint"}
 *     → 500:   {"error": "Internal Server Error"}
 *
 * 読み取り専用は二重に担保する。
 *   1. MySQL の権限（grant.sql の読み取り専用ユーザー）… 最終的な保証
 *   2. このファイルの SELECT 判定 …… 権限を張れなかった場合の最後の砦
 * どちらか一方でも書き込みは止まる。
 *
 * **エラーの中身は返さない。** 既存の口と同じ挙動（500 に潰す）を守る。
 * 詳細はサーバー内のログにだけ残す（event-support-server ADR 0001）。
 */

declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');

/** JSON を1つ返して終わる。 */
function respond(int $status, array $payload): void
{
    http_response_code($status);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE);
    exit;
}

// --- 1. POST 以外は 404（存在を主張しない。既存の口と同じ挙動）-----------------
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    respond(404, ['error' => 'Not Found']);
}

$config = require __DIR__ . '/config.php';

// --- 2. 認証 ------------------------------------------------------------------
// ヘッダー名は環境により HTTP_X_PROXY_KEY で届く。両方を見る。
$key = $_SERVER['HTTP_X_PROXY_KEY'] ?? '';
if ($key === '' && function_exists('getallheaders')) {
    foreach (getallheaders() as $name => $value) {
        if (strtolower($name) === 'x-proxy-key') {
            $key = $value;
            break;
        }
    }
}
// タイミング攻撃を避けるため hash_equals で比べる。
if ($key === '' || !hash_equals((string) $config['proxy_key'], (string) $key)) {
    respond(401, ['error' => 'Unauthorized']);
}

// --- 3. 入力の検証 ------------------------------------------------------------
$raw = file_get_contents('php://input');
$body = json_decode($raw === false ? '' : $raw, true);
if (!is_array($body) || !isset($body['sql']) || !is_string($body['sql'])) {
    respond(400, ['error' => 'Bad Request: sql(string) と params(array) が必要です']);
}
$params = $body['params'] ?? [];
if (!is_array($params)) {
    respond(400, ['error' => 'Bad Request: sql(string) と params(array) が必要です']);
}
$sql = trim($body['sql']);

// --- 4. SELECT 以外を拒む（権限の前の、もう1枚の壁）---------------------------
if (!is_select_only($sql)) {
    respond(403, ['error' => 'Forbidden: read-only endpoint']);
}

/**
 * SELECT 1文だけであることを判定する。
 *
 * 迷ったら拒む側に倒す。**この口を通すべき SQL は分析側で機械的に組み立てられており、
 * 人が書いた SQL を通す口ではない。** 誤って弾いても被害は「読めない」だけで済む。
 */
function is_select_only(string $sql): bool
{
    // 先頭のコメント（-- … / /* … */）を剥がしてから判定する
    $s = $sql;
    do {
        $before = $s;
        $s = ltrim($s);
        $s = preg_replace('#^/\*.*?\*/#s', '', $s) ?? $s;
        $s = preg_replace('/^--[^\n]*\n?/', '', $s) ?? $s;
        $s = preg_replace('/^#[^\n]*\n?/', '', $s) ?? $s;
    } while ($s !== $before);

    $s = rtrim($s);
    $s = rtrim($s, ';');

    // 文の区切りが残っていたら複文の疑い。文字列リテラル内の ; までは見分けないので拒む
    if (strpos($s, ';') !== false) {
        return false;
    }
    // SELECT で始まること（WITH … SELECT も通さない。分析側は使っていない）
    if (preg_match('/^select\s/i', $s) !== 1) {
        return false;
    }
    // 書き込み・定義変更の語が1つでもあれば拒む。
    // **語の終わりまで見る（\b で閉じる）。** 閉じ忘れると `created_at` の中の
    // create に反応して、正当な SELECT まで弾く（ローカル検証で実際に踏んだ）。
    $words = '/\b(insert|update|delete|replace|truncate|drop|alter|create|grant|revoke|'
        . 'rename|call|handler|prepare|execute)\b/i';
    if (preg_match($words, $s) === 1) {
        return false;
    }
    // 複数語のもの。語境界だけでは表せない
    $phrases = '/\b(load\s+data|into\s+outfile|into\s+dumpfile|lock\s+tables)\b|\bset\s+@/i';
    return preg_match($phrases, $s) !== 1;
}

// --- 5. 実行 ------------------------------------------------------------------
try {
    $pdo = new PDO(
        sprintf(
            'mysql:host=%s;port=%d;dbname=%s;charset=utf8mb4',
            $config['db_host'],
            (int) $config['db_port'],
            $config['db_name']
        ),
        $config['db_user'],
        $config['db_pass'],
        [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_EMULATE_PREPARES => false,
        ]
    );
    // **DATETIME は UTC で入っている。** セッションの時差設定に依存させない。
    $pdo->exec("SET time_zone = '+00:00'");

    $stmt = $pdo->prepare($sql);
    $stmt->execute(array_values($params));
    $rows = $stmt->fetchAll();

    respond(200, [
        'rows' => $rows,
        'affectedRows' => 0,   // 読み取り専用。常に 0
        'insertId' => null,
    ]);
} catch (Throwable $e) {
    // **中身は返さない。** 既存の口と同じく 500 に潰す。詳細はサーバーのログにだけ残す
    error_log('[readonly-proxy] ' . $e->getMessage());
    respond(500, ['error' => 'Internal Server Error']);
}
