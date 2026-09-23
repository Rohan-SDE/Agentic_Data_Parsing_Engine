import argparse
import getpass
import hashlib
import json
from pathlib import Path

from sqlalchemy import delete, select

from engine.config import settings
from engine.db import AuthSession, User, audit, session_factory
from engine.logging_config import configure
from engine.security import create_user, hasher


def main():
    parser = argparse.ArgumentParser(description="Agentic Data Parsing Engine")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="Apply database migrations")
    user = sub.add_parser("create-user", help="Create an account; prompts for a password")
    user.add_argument("username")
    user.add_argument("--admin", action="store_true")
    reset = sub.add_parser("reset-password")
    reset.add_argument("username")
    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    sub.add_parser("watch")
    sub.add_parser("cleanup", help="Remove expired sessions, old heartbeats and orphan files older than 24h")
    sub.add_parser("worker-health")
    sub.add_parser("maintenance", help="Run daily cleanup, with graceful shutdown and failure retries")
    sub.add_parser("verify-storage", help="Verify every registered source checksum; stop writers first")
    analysis = sub.add_parser("analyze", help="Analyze a local file without the API")
    analysis.add_argument("file", type=Path)
    analysis.add_argument("--output", required=True, type=Path)
    analysis.add_argument("--objective", default="Inspect data quality and unusual measurements.")
    args = parser.parse_args()
    configure()
    cfg = settings()
    cfg.prepare_dirs()
    if args.command == "migrate":
        from alembic.config import Config

        from alembic import command
        command.upgrade(Config("alembic.ini"), "head")
        print("Database is up to date.")
    elif args.command in {"create-user", "reset-password"}:
        password = getpass.getpass("Password (12–128 characters): ")
        if password != getpass.getpass("Confirm password: "):
            parser.error("Passwords differ.")
        if not 12 <= len(password) <= 128:
            parser.error("Password must contain 12–128 characters.")
        with session_factory()() as db:
            if args.command == "create-user":
                try:
                    user = create_user(db, args.username, password, args.admin)
                except ValueError as exc:
                    parser.error(str(exc))
            else:
                user = db.scalar(select(User).where(User.username == args.username))
                if not user:
                    parser.error("User not found.")
                user.password_hash = hasher.hash(password)
                db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
            audit(db, user.id, "cli." + args.command, user.id)
            db.commit()
        print("Account updated.")
    elif args.command == "serve":
        import uvicorn
        uvicorn.run("engine.app:app", host=args.host, port=args.port, access_log=False,
                    proxy_headers=cfg.trust_proxy_headers, forwarded_allow_ips=cfg.trusted_proxy_ips,
                    limit_concurrency=100, timeout_keep_alive=5)
    elif args.command == "worker":
        from engine.worker import run_worker
        run_worker(args.once)
    elif args.command == "watch":
        from engine.watcher import run_watcher
        run_watcher()
    elif args.command == "worker-health":
        from engine.worker import own_worker_healthy
        raise SystemExit(0 if own_worker_healthy() else 1)
    elif args.command == "cleanup":
        from engine.maintenance import cleanup
        removed = cleanup()
        print(f"Removed {removed} orphan files; expired metadata cleaned.")
    elif args.command == "maintenance":
        from engine.maintenance import run_maintenance
        run_maintenance()
    elif args.command == "verify-storage":
        from engine.maintenance import verify_storage
        result = verify_storage()
        print(json.dumps(result))
        raise SystemExit(1 if result["failures"] else 0)
    elif args.command == "analyze":
        from engine.analysis import analyze
        from engine.ingestion import filename_format
        if args.file.stat().st_size > cfg.max_upload_bytes:
            parser.error("File exceeds configured upload limit.")
        result = analyze(args.file, filename_format(args.file.name), args.objective, {}, cfg)
        with args.file.open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        result["dataset"] = {"filename": args.file.name, "sha256": sha}
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        print(f"Analyzed {result['rows']:,} records; wrote {args.output}.")


if __name__ == "__main__":
    main()
