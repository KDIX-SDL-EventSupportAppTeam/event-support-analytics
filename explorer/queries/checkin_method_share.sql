-- 当日（JST 10/16〜）のチェックイン手法の割合。qr = QR 読み取り、manual = コード入力
-- 対象は参加者＋出展者。checked_in_at は UTC 保存なので +9h
SELECT c.checkin_method,
       count(*)                                         AS checkins,
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct,
       count(DISTINCT c.user_id)                        AS users
FROM check_ins c
JOIN users u ON u.id = c.user_id AND u.role IN ('participant', 'exhibitor')
WHERE c.checked_in_at + INTERVAL 9 HOUR >= TIMESTAMP '2026-10-16'
GROUP BY ALL
ORDER BY checkins DESC
