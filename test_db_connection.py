# Requires: pip install psycopg2-binary pytest
# Run: pytest -q test_db_connection.py

import os
import psycopg2
import pytest


def _connect_with_params(params):
    # Use a short timeout so tests fail fast when DB is unreachable
    params = dict(params)
    params.setdefault("connect_timeout", 5)
    return psycopg2.connect(**params)


def _on_success(conn, label):
    """Print connection details and query school 102."""
    try:
        info = conn.get_dsn_parameters()
    except Exception:
        info = {}

    host = info.get("host")
    port = info.get("port")
    dbname = info.get("dbname") or info.get("database")
    user = info.get("user")

    print(f"Connection succeeded using: {label}")
    print("Connection details:")
    print(f"  host: {host}")
    print(f"  port: {port}")
    print(f"  database: {dbname}")
    print(f"  user: {user}")

    try:
        with conn.cursor() as cur:
            query = """
                SELECT *
                FROM schoolers.schools
                WHERE school_id = %s
            """
            cur.execute(query, (102,))
            rows = cur.fetchall()
            if cur.description:
                cols = [d[0] for d in cur.description]
            else:
                cols = []

            print("School 102 query output (rows):")
            if not rows:
                print("  (no rows returned)")
            else:
                for r in rows:
                    if cols:
                        print("  ", dict(zip(cols, r)))
                    else:
                        print("  ", r)
    except Exception as e:
        # Surface query errors but allow the test to fail with the message
        raise


def test_db_connection():
    """
    Try to connect using environment variables first. If that fails, fall back to
    the connection details shown in the provided screenshot and try again.

    Env priority:
      1. DATABASE_URL (full URL)
      2. PGHOST / PGPORT / PGDATABASE / PGUSER / PGPASSWORD

    Fallback (from screenshot):
      host=localhost, port=5432, database=postgres, user=ravi, password="ravi"
    """

    errors = []

    # 1) Try DATABASE_URL
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        try:
            conn = psycopg2.connect(dsn=db_url, connect_timeout=5)
            try:
                _on_success(conn, "DATABASE_URL")
            finally:
                conn.close()
            return
        except Exception as e:
            errors.append(("DATABASE_URL", str(e)))

    # 2) Try PG* environment variables
    env_params = {}
    if os.environ.get("PGHOST"):
        env_params["host"] = os.environ.get("PGHOST")
    if os.environ.get("PGPORT"):
        env_params["port"] = os.environ.get("PGPORT")
    if os.environ.get("PGDATABASE"):
        env_params["dbname"] = os.environ.get("PGDATABASE")
    if os.environ.get("PGUSER"):
        env_params["user"] = os.environ.get("PGUSER")
    if os.environ.get("PGPASSWORD"):
        env_params["password"] = os.environ.get("PGPASSWORD")

    if env_params:
        try:
            conn = _connect_with_params(env_params)
            try:
                _on_success(conn, "PG_* env")
            finally:
                conn.close()
            return
        except Exception as e:
            errors.append(("PG_* env", str(e)))

    # 3) Fallback to screenshot values
    fallback = {
        "host": "localhost",
        "port": 5432,
        "dbname": "schoolersdb",
        "user": "ravi",
        # Password appears blank in the screenshot; try empty string
        "password": "ravi",
    }
    try:
        conn = _connect_with_params(fallback)
        try:
            _on_success(conn, "fallback")
        finally:
            conn.close()
        return
    except Exception as e:
        errors.append(("fallback", str(e)))

    # If all attempts failed, fail the test and include errors
    msg_lines = ["All connection attempts failed:"]
    for label, err in errors:
        msg_lines.append(f"- {label}: {err}")

    pytest.fail("\n".join(msg_lines))
