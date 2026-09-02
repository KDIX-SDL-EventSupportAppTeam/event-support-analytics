-- 読み取り専用ユーザーを作る（event-support-analytics ADR 0001 案A′）
--
-- 使い方: <DB名> と <パスワード> を置き換えてから、さくらの phpMyAdmin の
--         「SQL」タブに貼って実行する。1回で終わる。
--
-- なぜ必要か: 既存のプロキシの鍵は全権であり、分析リポジトリから UPDATE が通ってしまう。
--             分析には SELECT しか要らないので、権限の側で落としておく。
--
-- **このユーザーに与えるのは SELECT だけ。** INSERT / UPDATE / DELETE は与えない。
-- users は id と role しか要らないので、列まで絞る（email / password_hash に触れない）。

CREATE USER 'bingo_ro'@'localhost' IDENTIFIED BY '<パスワード>';

-- 当日の監視に使うテーブル
GRANT SELECT ON `<DB名>`.`bingo_cards`          TO 'bingo_ro'@'localhost';
GRANT SELECT ON `<DB名>`.`bingo_cells`          TO 'bingo_ro'@'localhost';
GRANT SELECT ON `<DB名>`.`card_unlock_events`   TO 'bingo_ro'@'localhost';
GRANT SELECT ON `<DB名>`.`check_ins`            TO 'bingo_ro'@'localhost';
GRANT SELECT ON `<DB名>`.`booth_ratings`        TO 'bingo_ro'@'localhost';
GRANT SELECT ON `<DB名>`.`recommendation_scores` TO 'bingo_ro'@'localhost';

-- 事後の分析で追加で使うテーブル
GRANT SELECT ON `<DB名>`.`user_survey_answers`  TO 'bingo_ro'@'localhost';
GRANT SELECT ON `<DB名>`.`booths`               TO 'bingo_ro'@'localhost';
GRANT SELECT ON `<DB名>`.`categories`           TO 'bingo_ro'@'localhost';
GRANT SELECT ON `<DB名>`.`booth_tags`           TO 'bingo_ro'@'localhost';

-- users は列まで絞る。**email / password_hash は権限の側で読めなくしておく。**
GRANT SELECT (`id`, `role`) ON `<DB名>`.`users` TO 'bingo_ro'@'localhost';

FLUSH PRIVILEGES;

-- 確認（SELECT だけが並び、INSERT / UPDATE / DELETE が出てこないこと）
-- SHOW GRANTS FOR 'bingo_ro'@'localhost';
