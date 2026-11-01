"""SQL 実験室: 完全ダンプ（parquet）に DuckDB で SQL を投げて、表とグラフで見る（ローカル専用）。

使い方:
    .venv/bin/streamlit run explorer/sql_lab.py --server.port 8511 -- ../db-archive/2026-10-16-prod-event/full

- テーブル名そのまま（例: check_ins）は manifest の db_type で型を付けた view。int → BIGINT、datetime → TIMESTAMP、json → JSON
- raw.<テーブル名> は保存値そのまま（全列 string）
- 時刻は DB の保存値のまま（列によって JST/UTC が混在。補正はしない）
- sensitive/ は読まない
- イベント後アンケート: 「アンケート取り込み」ページで貼り付け → survey/ に保存（post_survey.py）。
  post_survey（1 人 1 行）/ post_survey_questions / post_survey_long（1 回答 1 行）の view で user_id から全テーブルと結合できる
- 保存したクエリは explorer/queries/*.sql（.gitignore 対象外。中身は SQL だけで個人データを含まない）
"""

import json
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

import importlib

import post_survey
import research

# Streamlit は再実行のたびに main スクリプトしか読み直さないので、分析モジュールの変更をサーバー再起動なしで反映する
post_survey = importlib.reload(post_survey)
research = importlib.reload(research)

HERE = Path(__file__).resolve().parent
QUERIES = HERE / "queries"
DEFAULT_DUMP = HERE.parent.parent / "db-archive" / "2026-10-16-prod-event" / "full"
EXPLORER_URL = "http://localhost:8510"

CAST = {
    "tinyint": "BIGINT", "smallint": "BIGINT", "int": "BIGINT", "bigint": "BIGINT",
    "double": "DOUBLE", "float": "DOUBLE", "decimal": "DOUBLE",
    "datetime": "TIMESTAMP", "timestamp": "TIMESTAMP", "date": "DATE",
    "json": "JSON",
}

EXAMPLE = """-- ブースごとのチェックイン数と平均評価
SELECT b.display_code, b.name,
       count(DISTINCT c.id) AS checkins,
       round(avg(r.rating), 2) AS avg_rating
FROM booths b
LEFT JOIN check_ins c ON c.booth_id = b.id
LEFT JOIN booth_ratings r ON r.booth_id = b.id
GROUP BY ALL
ORDER BY checkins DESC
"""


def dump_dir() -> Path:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    return Path(args[0]).resolve() if args else DEFAULT_DUMP


@st.cache_resource
def connect(path: str) -> tuple[duckdb.DuckDBPyConnection, dict]:
    root = Path(path)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    con = duckdb.connect()
    con.execute("CREATE SCHEMA raw")
    for name, t in manifest["tables"].items():
        file = str(root / t["file"]).replace("'", "''")
        con.execute(f"CREATE VIEW raw.{name} AS SELECT * FROM read_parquet('{file}')")
        cols = []
        for c in t["columns"]:
            to = CAST.get(c["db_type"])
            q = f'"{c["name"]}"'
            cols.append(f"TRY_CAST({q} AS {to}) AS {q}" if to else q)
        con.execute(f"CREATE VIEW main.{name} AS SELECT {', '.join(cols)} FROM raw.{name}")
    return con, manifest


def register_survey(con: duckdb.DuckDBPyConnection, root: Path) -> bool:
    """アンケートの view を貼り直す（取り込みのたびにファイルが変わるので毎回）。"""
    a, q = post_survey.answers_path(root), post_survey.questions_path(root)
    if not (a.exists() and q.exists()):
        return False
    fa, fq = (str(p).replace("'", "''") for p in (a, q))
    con.execute(f"CREATE OR REPLACE VIEW post_survey AS SELECT * FROM read_parquet('{fa}')")
    con.execute(f"CREATE OR REPLACE VIEW post_survey_questions AS SELECT * FROM read_parquet('{fq}')")
    con.execute("""CREATE OR REPLACE VIEW post_survey_long AS
        SELECT s.user_id, s.answered_at, u.q_key, q.question, q."order", u.answer,
               TRY_CAST(u.answer AS DOUBLE) AS answer_num
        FROM post_survey s
        JOIN (UNPIVOT post_survey ON COLUMNS('^q[0-9]+$') INTO NAME q_key VALUE answer) u USING (user_id)
        JOIN post_survey_questions q USING (q_key)""")
    return True


def saved_queries() -> dict[str, str]:
    QUERIES.mkdir(exist_ok=True)
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(QUERIES.glob("*.sql"))}


def chart(df: pd.DataFrame) -> None:
    cols = list(df.columns)
    num = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    c1, c2, c3, c4 = st.columns(4)
    kind = c1.selectbox("グラフ", ["棒", "折れ線", "散布図", "ヒストグラム", "箱ひげ"])
    x = c2.selectbox("X", cols)
    y = c3.selectbox("Y", ["（なし）"] + num, index=1 if num else 0)
    color = c4.selectbox("色分け", ["（なし）"] + cols)
    y = None if y == "（なし）" else y
    color = None if color == "（なし）" else color
    if kind == "ヒストグラム":
        fig = px.histogram(df, x=x, color=color)
    elif y is None:
        st.info("Y に数値の列を選んでください")
        return
    elif kind == "棒":
        fig = px.bar(df, x=x, y=y, color=color)
    elif kind == "折れ線":
        fig = px.line(df.sort_values(x), x=x, y=y, color=color, markers=True)
    elif kind == "散布図":
        fig = px.scatter(df, x=x, y=y, color=color)
    else:
        fig = px.box(df, x=x, y=y, color=color)
    st.plotly_chart(fig, width="stretch")


def sql_page() -> None:
    root = dump_dir()
    con, manifest = connect(str(root))
    has_survey = register_survey(con, root)

    with st.sidebar:
        # explorer に iframe で埋め込まれているので、親ウィンドウごと戻す
        st.markdown(f'<a href="{EXPLORER_URL}" target="_top">← ノード表示に戻る</a>', unsafe_allow_html=True)
        st.caption(f"ダンプ: `{root.name}` ・ 取得 {manifest['fetched_at']}")
        st.subheader("テーブル")
        st.caption("`名前` = 型付き / `raw.名前` = 保存値そのまま（全列 string）")
        for name, t in manifest["tables"].items():
            with st.expander(f"{name}（{t['rows']}行）"):
                st.code("\n".join(f"{c['name']}  {c['db_type']}" for c in t["columns"]), language=None)
        if has_survey:
            with st.expander("post_survey / _questions / _long（アンケート）"):
                st.code(", ".join(con.execute("SELECT * FROM post_survey LIMIT 0").df().columns)
                        + "\n\npost_survey_long: user_id, answered_at, q_key, question, order, answer, answer_num",
                        language=None)

        st.subheader("保存したクエリ")
        qs = saved_queries()
        pick = st.selectbox("読み込む", ["（選ぶ）"] + list(qs))
        if pick != "（選ぶ）" and st.button("エディタに読み込む"):
            st.session_state.sql = qs[pick]
            st.session_state.qname = pick

    st.title("SQL 実験室")
    st.caption("DuckDB の SQL。時刻は保存値のまま（JST/UTC 混在）。JSON 列は `reason_payload->>'strategy'` で取り出せる。⌘Enter で実行")

    sql = st.text_area("SQL", key="sql", value=st.session_state.get("sql", EXAMPLE), height=220)
    c1, c2, c3 = st.columns([1, 2, 1])
    run = c1.button("実行", type="primary")
    qname = c2.text_input("保存名", key="qname", placeholder="例: booth_checkins", label_visibility="collapsed")
    if c3.button("クエリを保存"):
        if re.fullmatch(r"[\w\-ぁ-んァ-ヶ一-龠ー]+", qname or ""):
            QUERIES.mkdir(exist_ok=True)
            (QUERIES / f"{qname}.sql").write_text(sql, encoding="utf-8")
            st.success(f"queries/{qname}.sql に保存しました")
        else:
            st.error("保存名は英数字・_・-・日本語だけにしてください")

    if run or "result" not in st.session_state:
        try:
            st.session_state.result = con.execute(sql).df()
            st.session_state.error = None
        except Exception as e:  # SQL エラーはそのまま見せる
            st.session_state.error = str(e)
    if st.session_state.get("error"):
        st.error(st.session_state.error)
        return

    df: pd.DataFrame = st.session_state.result
    st.caption(f"{len(df)} 行 × {len(df.columns)} 列（列見出しで並べ替え・虫眼鏡で検索）")
    tab_table, tab_chart = st.tabs(["表", "グラフ"])
    with tab_table:
        st.dataframe(df, width="stretch", height=480)
        st.download_button("CSV をダウンロード", df.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"{qname or 'result'}.csv", mime="text/csv")
    with tab_chart:
        if df.empty:
            st.info("結果が空です")
        else:
            chart(df)


def survey_page() -> None:
    root = dump_dir()
    st.title("アンケート取り込み")
    st.caption("スプレッドシートで見出し行ごと範囲を選んでコピー → 下に貼り付け。メアドでアプリのユーザーに紐付け、"
               "紐付いた人だけ survey/post_survey.parquet に保存する（メアド自体は保存しない）。保存済みの人は追加しない")

    status = st.empty()  # 取り込み後の件数で埋める

    text = st.text_area("貼り付け（タブ区切り・1 行目が見出し）", height=200)
    if st.button("取り込む", type="primary", disabled=not text.strip()):
        try:
            r = post_survey.import_paste(root, text)
        except Exception as e:
            st.error(str(e))
            return
        st.write(f"貼り付け {r.rows_in} 行 → 新規 {r.added} 人 ・ 保存済みのためスキップ {r.already} 人"
                 f" ・ 貼り付け内の重複 {r.duplicates_in_paste} 行（最後の行を採用）")
        if r.wrote:
            st.success("保存しました" + (f"（新しい質問 {len(r.new_questions)} 問）" if r.new_questions else ""))
        else:
            st.info("新規の回答者がいないので、ファイルは変更していません")
        if r.unlinked:
            st.warning(f"アプリのユーザーに無いメアド {len(r.unlinked)} 件を除外しました")
            st.dataframe(pd.DataFrame({"除外したメアド": r.unlinked}), hide_index=True)
        if r.invalid:
            st.warning(f"メアドの形でない値 {len(r.invalid)} 件を除外しました")
            st.dataframe(pd.DataFrame({"値": r.invalid}), hide_index=True)

    answers, questions = post_survey.load_existing(root)
    if len(answers):
        status.success(f"保存済み: {len(answers)} 人 ・ 質問 {len(questions)} 問（既存ファイルを読み込み済み。SQL では post_survey）")
    else:
        status.info("まだ取り込んでいません")

    if len(questions):
        st.subheader("質問と列名")
        st.dataframe(questions, hide_index=True)

    st.subheader("メアドから引く")
    email = st.text_input("メールアドレス", placeholder="アプリにログインしたメアド")
    if email:
        uid = post_survey.lookup_user(root, email)
        if not uid:
            st.warning("アプリのユーザーにこのメアドはありません")
            return
        con, _ = connect(str(root))
        register_survey(con, root)
        st.code(f"user_id = '{uid}'", language="sql")
        st.dataframe(con.execute("""
            SELECT u.role, u.display_name,
              (SELECT count(*) FROM check_ins WHERE user_id = $1) AS checkins,
              (SELECT count(*) FROM booth_ratings WHERE user_id = $1) AS ratings,
              (SELECT count(*) FROM bingo_cells c JOIN bingo_cards b ON b.id = c.card_id
                 WHERE b.user_id = $1 AND c.is_achieved = 1) AS bingo_achieved_cells,
              (SELECT count(*) FROM award_votes WHERE user_id = $1) AS award_votes,
              (SELECT count(*) FROM gacha_coin_uses WHERE user_id = $1) AS gacha_coins
            FROM users u WHERE u.id = $1""", [uid]).df(), hide_index=True)
        if len(answers) and uid in set(answers.user_id):
            st.dataframe(con.execute("SELECT question, answer FROM post_survey_long WHERE user_id = $1 ORDER BY \"order\"",
                                     [uid]).df(), hide_index=True)
        else:
            st.caption("アンケートの回答はありません")


def research_page() -> None:
    root = dump_dir()
    con, _ = connect(str(root))
    register_survey(con, root)
    research.page(con, root)


st.set_page_config(page_title="SQL 実験室", layout="wide")
st.navigation([st.Page(sql_page, title="SQL", icon="🧪", url_path="sql", default=True),
               st.Page(survey_page, title="アンケート取り込み", icon="📝", url_path="survey"),
               st.Page(research_page, title="研究分析", icon="🔬", url_path="research")]).run()
