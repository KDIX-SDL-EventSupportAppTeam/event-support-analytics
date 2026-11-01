-- 推薦方式ごとの満足度（★の分布・平均・ばらつき）。推薦先に行って付けた評価を、方式 × 推薦時の関心一致度で集計する
-- 対象: 参加者 + 出展者、当日（JST 10/16〜）。訪問 = マスが開いた後の、そのブースへの最初のチェックイン。その訪問に付いた★を使う
-- 比較用に「推薦外の訪問」（推薦枠への訪問に当たらないチェックイン）の★も同じ形で出す
-- check_ins.checked_in_at は UTC 保存なので +9h。card_unlock_events.created_at は JST
WITH p AS (
  SELECT id AS user_id FROM users WHERE role IN ('participant', 'exhibitor')
), ci AS (
  SELECT c.id, c.user_id, c.booth_id, c.checked_in_at + INTERVAL 9 HOUR AS t
  FROM check_ins c JOIN p USING (user_id)
  WHERE c.checked_in_at + INTERVAL 9 HOUR >= TIMESTAMP '2026-10-16'
), rt AS (
  SELECT checkin_id, max(rating) AS rating FROM booth_ratings GROUP BY 1
), slot AS (
  SELECT s.id AS slot_id, s.user_id, s.booth_id, s.interest_match, u.created_at AS unlock_t,
         coalesce(s.reason_payload->>'strategy', CASE WHEN u.phase = 'PRESURVEY' THEN 'PRESURVEY' ELSE u.strategy END) AS strategy
  FROM recommendation_scores s
  JOIN card_unlock_events u ON u.id = s.unlock_event_id
  JOIN p USING (user_id)
  WHERE s.was_assigned = 1 AND u.created_at >= TIMESTAMP '2026-10-16'
), hit AS (
  SELECT slot.slot_id, arg_min(ci.id, ci.t) AS checkin_id
  FROM slot JOIN ci ON ci.user_id = slot.user_id AND ci.booth_id = slot.booth_id AND ci.t >= slot.unlock_t
  GROUP BY 1
), rec AS (
  SELECT slot.strategy AS grp, slot.interest_match, hit.checkin_id, rt.rating
  FROM slot JOIN hit USING (slot_id) LEFT JOIN rt USING (checkin_id)
  WHERE slot.strategy IN ('RANDOM', 'DRSA', 'SER', 'EXPLORE')
), self AS (
  SELECT '推薦外の訪問' AS grp, NULL AS interest_match, ci.id AS checkin_id, rt.rating
  FROM ci LEFT JOIN rt ON rt.checkin_id = ci.id
  WHERE ci.id NOT IN (SELECT checkin_id FROM hit)
), allv AS (
  SELECT * FROM rec UNION ALL SELECT * FROM self
)
SELECT grp                                         AS strategy,
       CASE WHEN grouping(allv.interest_match) = 1 THEN '（全体）' ELSE coalesce(allv.interest_match, '—') END AS interest_match,
       count(*)                                    AS visits,
       count(rating)                               AS rated,
       round(count(rating) / count(*), 3)          AS rated_share,     -- 評価を付けた割合（低いと★は一部の人の声）
       count(*) FILTER (WHERE rating = 1)          AS star1,
       count(*) FILTER (WHERE rating = 2)          AS star2,
       count(*) FILTER (WHERE rating = 3)          AS star3,
       count(*) FILTER (WHERE rating = 4)          AS star4,
       round(avg(rating), 2)                       AS mean_rating,
       round(stddev_samp(rating), 2)               AS sd_rating,       -- ばらつき（標準偏差）
       round(count(*) FILTER (WHERE rating = 4) / nullif(count(rating), 0), 3)  AS star4_share,
       round(count(*) FILTER (WHERE rating <= 2) / nullif(count(rating), 0), 3) AS low_share
FROM allv
GROUP BY GROUPING SETS ((grp), (grp, allv.interest_match))
HAVING NOT (grp = '推薦外の訪問' AND grouping(allv.interest_match) = 0)  -- 推薦外には関心一致度が無いので全体だけ
ORDER BY CASE grp WHEN 'RANDOM' THEN 1 WHEN 'DRSA' THEN 2 WHEN 'SER' THEN 3 WHEN 'EXPLORE' THEN 4 ELSE 5 END,
         grouping(allv.interest_match) DESC, 2
