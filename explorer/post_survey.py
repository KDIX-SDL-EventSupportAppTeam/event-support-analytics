"""イベント後アンケート（Google フォーム → スプレッドシート）を取り込み、アプリのユーザーに紐付けて保存する。

- 入力: スプレッドシートからコピーした範囲（タブ区切り・1 行目が見出し）。メアドの列は見出しか値で自動判定
- 紐付け: メアドを小文字・前後空白除去で比べ、sensitive/users.parquet の email と一致した人だけ残す
- 保存先: <dump>/survey/post_survey.parquet（1 人 1 行。user_id・answered_at・q1..qN、全列 string）
          <dump>/survey/post_survey_questions.parquet（q_key ⇔ 質問文）
- メアドは保存しない（user_id で全テーブルと結合できる。メアドが要るときは sensitive/users から引く）
- 2 回目以降: すでに保存済みの user_id は追加しない。新規がなければファイルを書き換えない
- 同じ人が貼り付け内に複数行あるときは最後の行（フォームの回答順で最新）を採る
"""

import io
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_HEADER = re.compile(r"メール|mail", re.I)
TIME_HEADER = re.compile(r"タイムスタンプ|timestamp", re.I)


def survey_dir(dump: Path) -> Path:
    return dump / "survey"


def answers_path(dump: Path) -> Path:
    return survey_dir(dump) / "post_survey.parquet"


def questions_path(dump: Path) -> Path:
    return survey_dir(dump) / "post_survey_questions.parquet"


def norm_email(s: str) -> str:
    return str(s).strip().lower()


def parse_paste(text: str) -> pd.DataFrame:
    """スプレッドシートのコピー（TSV）を読む。セル内改行は "..." で囲まれて来るので csv として読む。"""
    df = pd.read_csv(io.StringIO(text.strip("\n")), sep="\t", dtype=str, keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]
    return df[~(df == "").all(axis=1)]


def find_email_column(df: pd.DataFrame) -> str:
    by_header = [c for c in df.columns if EMAIL_HEADER.search(c)]
    if by_header:
        return by_header[0]
    ratio = {c: df[c].str.strip().str.match(EMAIL_RE).mean() for c in df.columns}
    best = max(ratio, key=ratio.get)
    if ratio[best] < 0.5:
        raise ValueError("メールアドレスの列が見つかりません（見出しに「メール」を含めるか、メアドの列を含めて貼り付けてください）")
    return best


def load_app_emails(dump: Path) -> dict[str, str]:
    """メアド（正規化）→ user_id。"""
    u = pd.read_parquet(dump / "sensitive" / "users.parquet", columns=["id", "email"])
    return {norm_email(e): i for e, i in zip(u.email, u.id) if e}


def load_existing(dump: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    a, q = answers_path(dump), questions_path(dump)
    if a.exists() and q.exists():
        return pd.read_parquet(a), pd.read_parquet(q)
    return (pd.DataFrame(columns=["user_id", "answered_at"]),
            pd.DataFrame(columns=["q_key", "question", "order"]))


@dataclass
class ImportResult:
    rows_in: int = 0
    added: int = 0
    already: int = 0           # 保存済みの人（今回は追加しない）
    duplicates_in_paste: int = 0
    unlinked: list[str] = field(default_factory=list)   # アプリに無いメアド（除外した）
    invalid: list[str] = field(default_factory=list)    # メアドの形でない値（除外した）
    new_questions: list[str] = field(default_factory=list)
    wrote: bool = False


def import_paste(dump: Path, text: str) -> ImportResult:
    df = parse_paste(text)
    r = ImportResult(rows_in=len(df))
    email_col = find_email_column(df)
    time_col = next((c for c in df.columns if TIME_HEADER.search(c)), None)
    q_cols = [c for c in df.columns if c not in (email_col, time_col)]

    answers, questions = load_existing(dump)
    # 質問文 → q_key。既存の対応を引き継ぎ、新しい質問だけ番号を足す
    qmap = dict(zip(questions.question, questions.q_key))
    for c in q_cols:
        if c not in qmap:
            qmap[c] = f"q{len(qmap) + 1}"
            r.new_questions.append(c)

    emails = load_app_emails(dump)
    df["_email"] = df[email_col].map(norm_email)
    r.invalid = sorted({e for e in df._email if not EMAIL_RE.match(e)})
    df = df[df._email.str.match(EMAIL_RE)]
    r.unlinked = sorted({e for e in df._email if e not in emails})
    df = df[df._email.isin(emails)]
    r.duplicates_in_paste = int(df._email.duplicated().sum())
    df = df.drop_duplicates("_email", keep="last")

    df["user_id"] = df._email.map(emails)
    known = set(answers.user_id)
    r.already = int(df.user_id.isin(known).sum())
    df = df[~df.user_id.isin(known)]
    r.added = len(df)
    if not r.added:
        return r  # 新規なし → 既存ファイルをそのまま使う

    new = pd.DataFrame({"user_id": df.user_id.values,
                        "answered_at": df[time_col].values if time_col else None})
    for c in q_cols:
        new[qmap[c]] = df[c].values
    merged = pd.concat([answers, new], ignore_index=True)
    keys = sorted(qmap.values(), key=lambda k: int(k[1:]))
    merged = merged.reindex(columns=["user_id", "answered_at", *keys]).astype("string")
    text_of = {v: k for k, v in qmap.items()}
    qdf = pd.DataFrame({"q_key": keys, "question": [text_of[k] for k in keys],
                        "order": [int(k[1:]) for k in keys]})

    d = survey_dir(dump)
    d.mkdir(mode=0o700, exist_ok=True)
    for frame, path in ((merged, answers_path(dump)), (qdf, questions_path(dump))):
        tmp = path.with_suffix(".tmp")
        frame.to_parquet(tmp, index=False)
        os.chmod(tmp, 0o600)
        tmp.replace(path)
    (d / "README.json").write_text(json.dumps({
        "source": "イベント後アンケート（スプレッドシートからの貼り付け）",
        "key": "user_id（sensitive/users.email とメアドで照合。メアド自体は保存しない）",
        "rows": len(merged), "questions": len(keys),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    r.wrote = True
    return r


def lookup_user(dump: Path, email: str) -> str | None:
    return load_app_emails(dump).get(norm_email(email))
