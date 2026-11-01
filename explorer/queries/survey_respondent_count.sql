-- アンケート回答者数の確認（研究分析ページの「取り込み済み」「対象」と一致するはず）
-- 対象 = role が participant または exhibitor
SELECT
  count(*)                                                      AS saved_respondents,        -- 取り込み済み（メアドが紐付いた人）
  count(*) FILTER (WHERE u.role IN ('participant', 'exhibitor')) AS in_scope_respondents,     -- 研究分析の対象
  count(*) FILTER (WHERE u.role = 'participant')                AS participant_respondents,
  count(*) FILTER (WHERE u.role = 'exhibitor')                  AS exhibitor_respondents,
  count(*) FILTER (WHERE u.role NOT IN ('participant', 'exhibitor')) AS other_role_respondents,
  count(*) FILTER (WHERE u.id IS NULL)                          AS not_in_users,             -- users に居ない（通常 0）
  count(DISTINCT s.user_id)                                     AS distinct_user_ids          -- 重複が無ければ saved と同じ
FROM post_survey s
LEFT JOIN users u ON u.id = s.user_id
