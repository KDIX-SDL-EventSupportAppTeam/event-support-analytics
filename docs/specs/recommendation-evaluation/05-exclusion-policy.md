---
状態: 確定
最終更新: 2026-09-01
---

# 出展者・運営スタッフの分析除外方針

**今年（2026年）のデータから、来場者でないアカウント（運営・出展者・閲覧者）を
全集計から外す規則。** 去年データの[除外基準](../analytics-pipeline/03-extraction/exclusion-rules.md)
とは前提が違う（去年は role 列が無く目視確定だった。今年は `users.role` がある）。

---

## 1. 規則 — `role = 'participant'` だけを残す

今年の `users.role` は次のいずれか（正本は `event-support-server` `db/create-tables.sql` /
`docs/reference/auth.md`）。

| role | 誰か | 分析での扱い |
|---|---|---|
| `participant` | 来場者 | **残す** |
| `exhibitor` | 出展者 | 除外 |
| `manager` | 運営（旧 `admin` を含む） | 除外 |
| `viewer` | 運営（閲覧のみ） | 除外 |

**判定は「`participant` を残す」で行う（許可リスト）。** 新しい role が将来増えても、
既定で除外側に倒れる。`participant` の綴りは `rec_db._PARTICIPANT_ROLE` に1箇所だけ持つ。

---

## 2. server 側との一致

server は2つのやり方で同じことをしている。**どちらも残るのは `participant` だけ**である。

| server の場所 | 絞り方 |
|---|---|
| `src/routes/v1/admin/dashboard.ts`、`src/lib/bingo/fallback.ts`、`assignOuterCells.ts`、`ensureCard.ts`、`pickPreSurveyBooth.ts` | **SQL で** `users` を JOIN し `u.role = 'participant'` |
| `src/routes/v1/admin/analytics.ts` | SQL は `u.event_id = ?` のみ。**取得後に JS で** `role === 'participant'` に絞ってから集計する |

（フォールバック抽選の除外は server 内で E12 と呼ばれる。）
**analytics も同じ「`participant` だけを残す」に揃える。**

`rec_db.participants_only()` は実装上は `role != 'participant'` を**引く**書き方だが、
残る集合は許可リストと同じである。1点だけ意図的に違う:

> **users に居ない user_id / role 列が無い行は、analytics では除外しない**
> （`participants_only()` のフォールバック）。

当日監視でテーブル間の取得に数秒のずれが出る（[02](02-data-source.md) §4）ため、
`users` の取得が一瞬遅れた回に来場者のチェックインを丸ごと落とすより、
数件のスタッフ行が混じるほうが監視としては安全側、という判断。
server は INNER JOIN なので該当行は落ちる。**事後分析はダンプ1回ぶんで
`users` が揃っているため、この差は出ない。**

---

## 3. `users.event_id` では絞らない

出展者・運営アカウントも来場者と**同じ `event_id` を持つ**
（`users.event_id` は所属イベントであって役割ではない）。
`event_id` で絞っても運営・出展者は落ちない。落とすのは role だけの仕事である。

`rec_db.scope_to_event()` は `users` テーブルを `event_id` で絞らない
（同関数の docstring と `tests/test_rec_db.py::test_sql_source_works_through_load_tables` を参照）。
`card_unlock_events` / `bingo_cells` は `event_id` 列を持たないため、
`bingo_cards` 経由で解決した `event_id` で絞る（`CARD_KEYED_TABLES`）。

`recommendation_scores` は `event_id` 列も `card_id` 列も持たない（持つのは
`unlock_event_id`）ため、`unlock_event_id` → `card_unlock_events.id` → `card_id`
→ `bingo_cards.event_id` で `event_id` を解決してから絞る
（`rec_db.attach_scores_event_id()`。`load_tables()` は `recommendation_scores` を
要求されると `names` に無くても `card_unlock_events` / `bingo_cards` を取得する）。
`user_id` は割らないため `event_id` だけをマージする（`attach_card_owner()` は流用しない）。
role による除外は `user_id` があるので従来どおり効く。

> **イベントを解決できない行は除外する。** `unlock_event_id` から
> `card_unlock_events` / `bingo_cards` を辿れない行は `event_id` が NaN になり、
> 絞り込み時に落ちる（`card_unlock_events` の解決失敗と同じ扱い）。

これは §2 の `participants_only()`（**残す**方に倒す）と**逆向き**である。意図的に分けている。
role は「この人がスタッフだと**分かっている**」ときだけ落とせばよいが、イベント絞り込みは
「このイベントの行だと**言える**」ものだけを入れる話であり、所属を示せない行を混ぜると
他イベントのデータが紛れる。**当日 `card_unlock_events` の取得が一拍遅れた回は、
直近の推薦が数件欠けうる**（[02](02-data-source.md) §4）。欠けるのは次の更新までである。

---

## 4. 去年の pid 除外 UI との関係

`src/dashboard.py` の「除外する pid（運営・出展者）」テキスト欄と
`build_tables.detect_staff_candidates()` は、**去年データ専用**である。

| | 去年（analytics-pipeline） | 今年（recommendation-evaluation） |
|---|---|---|
| role 列 | **無い** | ある（`users.role`） |
| 除外方法 | 開場前チェックイン等の手がかりで候補を出し、**人が pid を確定**して手入力 | `role = 'participant'` で**自動** |
| 実装 | `dashboard.py` の pid 欄 / `detect_staff_candidates()` | `rec_db.participants_only()`（`load_tables()` が既定で適用） |

**今年の2画面（当日監視・事後分析）に pid 手入力欄は無い。** 不要である。
今年データに対して `detect_staff_candidates()` 系のヒューリスティックは使わない。

---

## 5. 除外しないもの

[去年の規則](../analytics-pipeline/03-extraction/exclusion-rules.md#除外しないもの)と同じ。
チェックイン0件の参加者、カード未生成の参加者、単発訪問者は**残す**。
これらは「アプリを使わなかった層」の規模を示すため、分析上むしろ重要である。
