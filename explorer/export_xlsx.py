"""完全ダンプを 1 つの .xlsx に書き出す（値は加工しない）。

使い方（event-support-analytics で）:
    .venv/bin/python explorer/export_xlsx.py ../db-archive/2026-10-16-prod-event/full
    .venv/bin/python explorer/export_xlsx.py ../db-archive/2026-10-16-prod-event/full --encrypt   # パスワード付き（端末で入力）

- シート: README / 列定義 / テーブルごと（DB と同じ正規化構造）。sensitive/ があればメール列込みの行を使う
- 全セルを文字列セルで書く（ID・日時・"0011"・"=..." を Excel に解釈させない）。NULL は空セル
- Excel の 1 セル上限（32,767 文字）を超える値は「長い値」シートに分割して全文を残す
- 書き出し後に読み戻し、全セルが元データと一致するか検証する
- **値は標準出力に一切出さない。** 出すのはシート名・行数・検証結果の件数だけ
- 出力ディレクトリは 700、ファイルは 600。Google スプレッドシートで使う場合も、アップロードは自分の手で行う
"""

import getpass
import json
import os
import sys
import tempfile
from pathlib import Path

import pyarrow.parquet as pq
from openpyxl import Workbook, load_workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font

MAX_CELL = 32767
CHUNK = 32000


def text_cell(ws, v, bold=False):
    c = WriteOnlyCell(ws, value=v)
    if v is not None:
        c.data_type = "s"   # "=" で始まっても数式にしない
    if bold:
        c.font = Font(bold=True)
    return c


def load(dump: Path):
    m = json.loads((dump / "manifest.json").read_text(encoding="utf-8"))
    sens = m.get("sensitive", {})
    tables = {}
    for t, info in m["tables"].items():
        src = sens.get(t, info)
        tb = pq.read_table(dump / src["file"])
        tables[t] = {"info": src, "cols": tb.column_names, "rows": tb.to_pylist(), "sensitive": t in sens}
    return m, tables


def build(m, tables, path: Path):
    wb = Workbook(write_only=True)
    long_vals, fixed = [], {"illegal": 0}

    readme = wb.create_sheet("README")
    lines = [
        ("イベントID", m["event_id"]), ("取得日時(UTC)", m["fetched_at"]), ("形式", m["format"]),
        ("注意", "時刻は DB の保存値そのまま。列によって JST/UTC が混在する（補正していない）"),
        ("注意", "全セル文字列。数値も文字として入っている。NULL は空セル"),
        ("取らなかったテーブル", ", ".join(m.get("excluded_tables", []))),
        ("", ""), ("シート", "行数 / 取らなかった列 / メール列 / 取得条件 / sha256"),
    ]
    for t, d in tables.items():
        i = d["info"]
        lines.append((t, f"{len(d['rows'])} / {', '.join(i.get('excluded_columns', [])) or '-'} / "
                         f"{', '.join(i.get('email_columns', [])) or '-'} / {m['tables'][t]['scope']} / {i['sha256']}"))
    for k, v in lines:
        readme.append([text_cell(readme, k, bold=True), text_cell(readme, v)])

    cdef = wb.create_sheet("列定義")
    cdef.append([text_cell(cdef, h, bold=True) for h in ("テーブル", "列", "DB の型", "主キー")])
    for t, d in tables.items():
        pk = set(d["info"].get("primary_key", []))
        for c in d["info"]["columns"]:
            cdef.append([text_cell(cdef, x) for x in (t, c["name"], c["db_type"], "PK" if c["name"] in pk else "")])

    for t, d in tables.items():
        ws = wb.create_sheet(t[:31])
        ws.freeze_panes = "A2"
        cols = d["cols"]
        for i, c in enumerate(cols):
            ws.column_dimensions[ws_col(i)].width = 18 if c != "name" else 40
        ws.append([text_cell(ws, c, bold=True) for c in cols])
        pk = d["info"].get("primary_key") or cols[:1]
        for r in d["rows"]:
            out = []
            for c in cols:
                v = r.get(c)
                if v is not None and ILLEGAL_CHARACTERS_RE.search(v):
                    fixed["illegal"] += 1
                    v = ILLEGAL_CHARACTERS_RE.sub("�", v)
                if v is not None and len(v) > MAX_CELL:
                    key = "|".join(str(r.get(k)) for k in pk)
                    for n, i in enumerate(range(0, len(v), CHUNK)):
                        long_vals.append((t, key, c, n + 1, v[i:i + CHUNK]))
                    v = f"<長い値: 「長い値」シートに全文 ({len(v)} 文字)>"
                out.append(text_cell(ws, v))
            ws.append(out)
        if cols:
            ws.auto_filter.ref = f"A1:{ws_col(len(cols) - 1)}{len(d['rows']) + 1}"

    if long_vals:
        lv = wb.create_sheet("長い値")
        lv.append([text_cell(lv, h, bold=True) for h in ("テーブル", "主キー", "列", "分割番号", "値")])
        for row in long_vals:
            lv.append([text_cell(lv, str(x)) for x in row])
    wb.save(path)
    return len(long_vals), fixed["illegal"]


def ws_col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def verify(path: Path, tables) -> tuple[int, int]:
    """読み戻して全セルを照合。(一致しないセル数, 空文字→空セルになったセル数)"""
    wb = load_workbook(path, read_only=True)
    diff = empty = 0
    for t, d in tables.items():
        ws = wb[t[:31]]
        it = ws.iter_rows(values_only=True)
        header = next(it)
        if list(header) != d["cols"]:
            diff += 1
        n = 0
        for r, got in zip(d["rows"], it):
            n += 1
            for c, g in zip(d["cols"], got):
                v = r.get(c)
                if v == "" and g is None:
                    empty += 1
                elif v is not None and len(v) > MAX_CELL:
                    continue
                elif (v if v is None else ILLEGAL_CHARACTERS_RE.sub("�", v)) != g:
                    diff += 1
        diff += abs(n - len(d["rows"]))
    wb.close()
    return diff, empty


def main(dump: Path, encrypt: bool) -> None:
    m, tables = load(dump)
    out_dir = dump / "export"
    out_dir.mkdir(exist_ok=True)
    os.chmod(out_dir, 0o700)
    name = f"event_{m['event_id'][:8]}_{m['fetched_at'][:10].replace('-', '')}.xlsx"
    path = out_dir / name
    pw = None
    if encrypt:
        pw = getpass.getpass("xlsx のパスワード: ")
        if pw != getpass.getpass("もう一度: "):
            sys.exit("パスワードが一致しない")
    fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=out_dir)
    os.close(fd)
    os.chmod(tmp, 0o600)
    tmp = Path(tmp)
    try:
        n_long, n_illegal = build(m, tables, tmp)
        diff, empty = verify(tmp, tables)
        if pw:
            import msoffcrypto
            with open(tmp, "rb") as src, open(path, "wb") as dst:
                msoffcrypto.format.ooxml.OOXMLFile(src).encrypt(pw, dst)
        else:
            tmp.replace(path)
        os.chmod(path, 0o600)
    finally:
        if tmp.exists():
            tmp.unlink()
    for t, d in tables.items():
        print(f"  {t}: {len(d['rows'])} 行{'（メール列込み）' if d['sensitive'] else ''}")
    print(f"検証: 一致しないセル {diff} / 空文字→空セル {empty} / 長い値の分割 {n_long} / 制御文字の置換 {n_illegal}")
    print(f"出力: {path}{'（パスワード付き）' if pw else ''}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)
    main(Path(args[0]), "--encrypt" in sys.argv)
