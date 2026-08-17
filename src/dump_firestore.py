"""Firestore からの一括抽出スクリプト。

仕様: docs/.sdd/03-extraction/dump-spec.md

- 読み取り専用。書き込みAPIは一切呼ばない
- コレクショングループクエリで一括取得する（ユーザーごとのラウンドトリップを避ける）
- メールアドレスは仮名ID（u0001 形式）に置換し、対応表は生成しない
- password_hash / email / last_checkin_timestamp は出力しない
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

DEFAULT_PROJECT = "protofes"
DEFAULT_DATABASE = "(default)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument(
        "--out-dir",
        default="data/raw",
        help="出力先ディレクトリ (default: data/raw)",
    )
    return parser.parse_args()


def booth_no_from_image_url(image_url: str | None) -> int | None:
    """`/booth_images/22.png` -> 22"""
    if not image_url:
        return None
    m = re.search(r"(\d+)\.\w+$", image_url)
    return int(m.group(1)) if m else None


def to_iso_utc(ts) -> str | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_pid_map(emails: list[str]) -> dict[str, str]:
    """メールアドレスのソート順で u0001... を決定的に採番する。"""
    ordered = sorted(set(emails))
    return {email: f"u{i:04d}" for i, email in enumerate(ordered, start=1)}


def dump(project: str, database: str) -> dict:
    from google.cloud import firestore  # imported lazily so tests don't require the package

    db = firestore.Client(project=project, database=database)

    # --- booths ---
    booths = []
    for doc in db.collection("booths").stream():
        d = doc.to_dict()
        booths.append(
            {
                "booth_id": doc.id,
                "booth_no": booth_no_from_image_url(d.get("booth_image_url")),
                "booth_name": d.get("booth_name"),
                "booth_description": d.get("booth_description"),
                "booth_emoji": d.get("booth_emoji"),
            }
        )

    # --- users (email intentionally NOT persisted beyond pid resolution) ---
    user_docs = list(db.collection("users").stream())
    emails = [doc.id for doc in user_docs]
    pid_of = build_pid_map(emails)

    users = []
    for doc in user_docs:
        d = doc.to_dict()
        users.append(
            {
                "pid": pid_of[doc.id],
                "age": d.get("age"),
                "gender": d.get("gender"),
                "genre": d.get("genre"),
                "gachapon_coins_spent": d.get("gachapon_coins_spent"),
            }
        )

    # --- checkins (collection group) ---
    checkins = []
    for doc in db.collection_group("checkins").stream():
        d = doc.to_dict()
        owner_email = doc.reference.parent.parent.id
        pid = pid_of.get(owner_email)
        if pid is None:
            continue  # orphaned doc under an unknown/removed user
        checkins.append(
            {
                "pid": pid,
                "booth_id": d.get("booth_id", doc.id),
                "ts_utc": to_iso_utc(d.get("timestamp")),
            }
        )

    # --- bingo_card (collection group; no user_id field, must resolve via parent) ---
    bingo_card = []
    for doc in db.collection_group("bingo_card").stream():
        d = doc.to_dict()
        owner_email = doc.reference.parent.parent.id
        pid = pid_of.get(owner_email)
        if pid is None:
            continue
        bingo_card.append(
            {
                "pid": pid,
                "booth_id": d.get("booth_id", doc.id),
                "position": d.get("position"),
                "is_recommendation": bool(d.get("is_recommendation", False)),
            }
        )

    # --- awards (collection group) ---
    awards = []
    for doc in db.collection_group("awards").stream():
        d = doc.to_dict()
        owner_email = doc.reference.parent.parent.id
        pid = pid_of.get(owner_email)
        if pid is None:
            continue
        awards.append(
            {
                "pid": pid,
                "award_name": d.get("award_name", doc.id),
                "booth_id": d.get("booth_id"),
                "ts_utc": to_iso_utc(d.get("timestamp")),
            }
        )

    # --- user_status ---
    user_status = []
    for doc in db.collection("user_status").stream():
        d = doc.to_dict()
        pid = pid_of.get(doc.id)
        if pid is None:
            continue
        user_status.append({"pid": pid, "vote_finalized": d.get("vote_finalized")})

    counts = {
        "users": len(users),
        "booths": len(booths),
        "user_status": len(user_status),
        "checkins": len(checkins),
        "awards": len(awards),
        "bingo_card": len(bingo_card),
    }

    # emails / pid_of are dropped here; nothing beyond this point holds them.
    return {
        "meta": {
            "dumped_at": datetime.now(JST).isoformat(),
            "project": project,
            "database": database,
            "counts": counts,
        },
        "booths": booths,
        "users": users,
        "checkins": checkins,
        "bingo_card": bingo_card,
        "awards": awards,
        "user_status": user_status,
    }


def main() -> None:
    args = parse_args()
    result = dump(args.project, args.database)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"dump_{stamp}.json"
    if out_path.exists():
        raise FileExistsError(f"{out_path} already exists; refusing to overwrite")

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"wrote {out_path}")
    print(json.dumps(result["meta"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
