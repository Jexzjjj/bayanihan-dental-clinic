"""Database connection at mga helper.

Lahat ng koneksyon sa SQLite dumadaan dito para iisa lang ang setup.
"""
import os
import sqlite3
from contextlib import contextmanager

from flask import current_app, g

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def connect(path):
    """Gumawa ng bagong connection.

    isolation_level=None ibig sabihin autocommit tayo, tapos tayo mismo
    ang magsasabi ng BEGIN at COMMIT kapag transaction (tingnan ang transaction()).
    """
    conn = sqlite3.connect(path, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row  # para pwedeng row["name"] imbes na row[1]
    # off by default ang foreign keys sa SQLite, kaya kailangan i-on sa bawat connection
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    """Isang connection lang bawat request; ginagamit ulit kung nabuksan na."""
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_app(app):
    # isasara yung connection pagkatapos ng bawat request
    app.teardown_appcontext(close_db)


def init_db(path, seed=False):
    """Gumawa ng bagong database. Babala: buburahin nito yung lumang data."""
    conn = connect(path)
    try:
        with open(SCHEMA_PATH, encoding="utf-8") as schema_file:
            conn.executescript(schema_file.read())
        if seed:
            import seed as seed_module  # dito na i-import para walang circular import

            seed_module.load(conn)
    finally:
        conn.close()


@contextmanager
def transaction(conn):
    """All-or-nothing na transaction.

    BEGIN IMMEDIATE = kinukuha agad ang write lock, kaya yung check at yung
    insert/update ay hindi masisingitan ng ibang request sa gitna.
    Pag may nag-error kahit isang step, ROLLBACK lahat para walang kalahating save.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
