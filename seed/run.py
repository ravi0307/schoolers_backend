"""CLI for the Schoolers reset + seed workflow.

    python -m seed.run reset --dry-run
    ALLOW_DB_RESET=true python -m seed.run reset
    ALLOW_DB_RESET=true python -m seed.run seed
    ALLOW_DB_RESET=true python -m seed.run all

A real (writing) reset is refused unless ALLOW_DB_RESET=true and the target
database looks local/development.
"""
from __future__ import annotations

import argparse
import datetime
import os
import shutil
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent
BACKUP_ROOT = ROOT / "backups"

LIKELY_PG_DUMPS = [
    "/Applications/Postgres.app/Contents/Versions/17/bin/pg_dump",
    "/opt/homebrew/opt/postgresql@17/bin/pg_dump",
    "/usr/local/opt/postgresql@17/bin/pg_dump",
]


def database_url() -> str:
    sys.path.insert(0, str(ROOT))
    from common.config import settings
    return settings.DATABASE_URL


def find_pg_dump() -> str | None:
    for candidate in LIKELY_PG_DUMPS:
        if Path(candidate).exists():
            return candidate
    return shutil.which("pg_dump")


def guard_local(url_str: str) -> None:
    url = make_url(url_str)
    host = url.host or "localhost"
    if os.environ.get("NODE_ENV") == "production" or os.environ.get("ENV") == "production":
        sys.exit("Refusing: environment reports production.")
    if host not in {"localhost", "127.0.0.1", "::1"}:
        sys.exit(f"Refusing: database host {host!r} is not local.")


def require_reset_gate() -> None:
    if os.environ.get("ALLOW_DB_RESET") != "true":
        sys.exit(
            "Refusing to write. Set ALLOW_DB_RESET=true to confirm a local reset."
        )


def backup(url_str: str) -> Path | None:
    pg_dump = find_pg_dump()
    if not pg_dump:
        print("WARN: no pg_dump found; skipping automatic backup.")
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = BACKUP_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "schoolersdb.sql"
    url = make_url(url_str)
    cmd = [pg_dump, "-h", url.host or "localhost", "-p", str(url.port or 5432),
           "-U", url.username or "ravi", "-d", url.database, "-f", str(out_file)]
    env = {**os.environ, "PGPASSWORD": url.password or ""}
    print(f"Backing up to {out_file} via {pg_dump}")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"WARN: pg_dump failed ({proc.returncode}): {proc.stderr.strip()}")
        return None
    (BACKUP_ROOT / ".last_backup").write_text(stamp)
    print(f"Backup ok ({out_file.stat().st_size} bytes)")
    return out_file


def cmd_reset(args) -> None:
    from seed import reset as reset_mod

    url_str = database_url()
    guard_local(url_str)
    engine = reset_mod.make_engine(url_str)
    if args.dry_run:
        result = reset_mod.reset(engine, dry_run=True)
        _print_counts("Rows that WOULD be deleted (dry run)", result["before"])
        print("Dry run only; nothing was written.")
        return
    require_reset_gate()
    if not args.no_backup:
        backup(url_str)
    result = reset_mod.reset(engine, dry_run=False)
    _print_counts("Rows deleted", [
        reset_mod.TableCount(t, n) for t, n in result["deleted"].items()
    ])
    _print_counts("Rows remaining", result["after"])
    print(f"Sequences reset: {len(result['sequences'])}")


def _print_counts(title: str, rows) -> None:
    print(f"\n{title}:")
    for row in rows:
        print(f"  {row.table:<28} {row.rows}")


def cmd_seed(args) -> None:
    from seed import seed as seed_mod
    seed_mod.run(dry_run=args.dry_run)


def cmd_verify(args) -> None:
    from seed import verify as verify_mod
    verify_mod.run()


def cmd_all(args) -> None:
    cmd_reset(args)
    cmd_seed(args)
    if not args.dry_run:
        cmd_verify(args)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p_reset = sub.add_parser("reset", help="remove all tenant data")
    p_reset.add_argument("--dry-run", action="store_true")
    p_reset.add_argument("--no-backup", action="store_true")
    p_reset.set_defaults(func=cmd_reset)

    p_seed = sub.add_parser("seed", help="create the two schools via the real API")
    p_seed.add_argument("--dry-run", action="store_true")
    p_seed.set_defaults(func=cmd_seed)

    p_verify = sub.add_parser("verify", help="assert the seeded state")
    p_verify.set_defaults(func=cmd_verify)

    p_all = sub.add_parser("all", help="reset then seed then verify")
    p_all.add_argument("--dry-run", action="store_true")
    p_all.add_argument("--no-backup", action="store_true")
    p_all.set_defaults(func=cmd_all)

    return ap


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
