"""check_sql の単体テスト。`python3 -I -m unittest test_main` で走る（依存なし）。"""
import sys, os, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from main import check_sql

EID = ["25694b10-46b9-418c-8a26-fdfb8a3c400a"]


class CheckSql(unittest.TestCase):
    def ok(self, sql, params=()):
        self.assertIsNone(check_sql(sql, list(params)), sql)

    def ng(self, sql, params=()):
        self.assertIsNotNone(check_sql(sql, list(params)), sql)

    def test_recommend_and_analytics_shapes_pass(self):
        self.ok("SELECT `id`, `user_id`, `booth_id`, `event_id`, `visit_order`, `checked_in_at` FROM `check_ins` WHERE `event_id` = ?", EID)
        self.ok("SELECT `id`, `role` FROM `users`")
        self.ok("SELECT `card_id`, `booth_id`, `source` FROM `bingo_cells`")
        self.ok("SELECT `id`, `created_at` FROM `recommendation_scores`;")

    def test_parent_scoped_bingo_cells(self):
        sub = " WHERE `card_id` IN (SELECT `id` FROM `bingo_cards` WHERE `event_id` = ?)"
        self.ok("SELECT `card_id`, `booth_id`, `source` FROM `bingo_cells`" + sub, EID)
        self.ng("SELECT `card_id` FROM `bingo_cells`" + sub)                     # params 不足
        self.ng("SELECT `id` FROM `check_ins`" + sub, EID)                        # 組み合わせ外の表
        self.ng("SELECT `card_id` FROM `bingo_cells` WHERE `card_id` IN (SELECT `id` FROM `users` WHERE `event_id` = ?)", EID)
        self.ng("SELECT `card_id` FROM `bingo_cells` WHERE `booth_id` IN (SELECT `id` FROM `bingo_cards` WHERE `event_id` = ?)", EID)
        self.ng("SELECT `card_id` FROM `bingo_cells` WHERE `card_id` IN (SELECT `email` FROM `users` WHERE `event_id` = ?)", EID)

    def test_write_and_ddl_rejected(self):
        for s in ["UPDATE users SET role='manager'", "DELETE FROM check_ins", "DROP TABLE users",
                  "INSERT INTO booths VALUES (1)", "SELECT `id` FROM `users`; DELETE FROM users",
                  "SELECT `id` FROM `users` UNION SELECT `email` FROM `users`"]:
            self.ng(s)

    def test_forbidden_columns_and_tables(self):
        self.ng("SELECT `email` FROM `users`")
        self.ng("SELECT `id`, `password_hash` FROM `users`")
        self.ng("SELECT * FROM `users`")
        self.ng("SELECT `token` FROM `email_verification_tokens`")
        self.ng("SELECT `id` FROM `organizers`")

    def test_where_and_params(self):
        self.ng("SELECT `id` FROM `booths` WHERE `event_id` = ?")          # params 不足
        self.ng("SELECT `id` FROM `booths`", EID)                           # params 過多
        self.ng("SELECT `id` FROM `booths` WHERE `event_id` = 'x' OR 1=1")
        self.ng("SELECT `id` FROM `booths` WHERE `event_id` = ?", [True])
        self.ng("SELECT SLEEP(10)")
        self.ng("SELECT `id` FROM `booths` INTO OUTFILE '/tmp/x'")


if __name__ == "__main__":
    unittest.main()
