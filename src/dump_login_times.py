"""去年（protofes）の来場日推定用に、ユーザーごとの時刻情報を読み取り専用で抽出する。

dump_firestore.py の補助。メールアドレスは同じ規則（ソート順で u0001...）で仮名IDに置換し、
出力には残さない。書き込みAPIは一切呼ばない。

取得するもの（すべて pid 単位、UTC の ISO 文字列）:
- users ドキュメントの create_time / update_time（Firestore が自動で持つメタデータ）
- user_status ドキュメントの create_time / update_time
- bingo_card / checkins / awards サブコレクションの最初と最後の create_time
- Firebase Authentication の creationTime / lastSignInTime（--with-auth 指定時。firebase-admin が必要）
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dump_firestore import DEFAULT_DATABASE, DEFAULT_PROJECT, build_pid_map

JST = ZoneInfo("Asia/Tokyo")


def iso(ts) -> str | None:
    if ts is None:
        return None
    if hasattr(ts, "ToDatetime"):  # protobuf Timestamp（collection_group の create_time）
        ts = ts.ToDatetime(tzinfo=timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def owner_email(doc) -> str | None:
    """users/{email}/<sub>/{id} の {email} を返す。"""
    parent = doc.reference.parent.parent
    return parent.id if parent is not None else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--database", default=DEFAULT_DATABASE)
    ap.add_argument("--out-dir", default="data/raw")
    ap.add_argument("--with-auth", action="store_true", help="Firebase Authentication の時刻も取る")
    args = ap.parse_args()

    from google.cloud import firestore

    db = firestore.Client(project=args.project, database=args.database)

    user_docs = list(db.collection("users").stream())
    pid_of = build_pid_map([d.id for d in user_docs])
    rows = {
        pid_of[d.id]: {"pid": pid_of[d.id], "user_create": iso(d.create_time), "user_update": iso(d.update_time)}
        for d in user_docs
    }

    for d in db.collection("user_status").stream():
        pid = pid_of.get(d.id)
        if pid:
            rows[pid]["status_create"] = iso(d.create_time)
            rows[pid]["status_update"] = iso(d.update_time)

    for sub in ("bingo_card", "checkins", "awards"):
        for d in db.collection_group(sub).stream():
            pid = pid_of.get(owner_email(d))
            if not pid:
                continue
            t = iso(d.create_time)
            r = rows[pid]
            r[f"{sub}_first"] = min(filter(None, [r.get(f"{sub}_first"), t]), default=None)
            r[f"{sub}_last"] = max(filter(None, [r.get(f"{sub}_last"), t]), default=None)

    if args.with_auth:
        import firebase_admin
        from firebase_admin import auth

        firebase_admin.initialize_app(options={"projectId": args.project})
        try:
            auth_users = list(auth.list_users().iterate_all())
        except auth.ConfigurationNotFoundError:
            print("Firebase Authentication が未設定のプロジェクトなので、認証側の時刻は取らない")
            auth_users = []
        for u in auth_users:
            pid = pid_of.get(u.email)
            if not pid:
                continue
            m = u.user_metadata
            to = lambda ms: iso(datetime.fromtimestamp(ms / 1000, tz=timezone.utc)) if ms else None  # noqa: E731
            rows[pid]["auth_created"] = to(m.creation_timestamp)
            rows[pid]["auth_last_sign_in"] = to(m.last_sign_in_timestamp)

    out = {
        "meta": {"dumped_at": datetime.now(JST).isoformat(), "project": args.project, "users": len(rows)},
        "users": sorted(rows.values(), key=lambda r: r["pid"]),
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"login_times_{datetime.now(JST):%Y%m%d_%H%M%S}.json"
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path} ({len(rows)} users)")


if __name__ == "__main__":
    main()
