"""Every named query is planned by a real PostgreSQL (EXPLAIN): syntax, tables and columns.

Needs a database with the schema loaded; without one (no DB_PASSWORD, or no server answering)
the tests are skipped. EXPLAIN plans a statement without running it, so nothing is read or
written, and each check is rolled back.
"""

import os
import re

import pytest

import mlb.sql
import nba.sql

PACKAGES = {"nba": nba.sql, "mlb": mlb.sql}
SEARCH_PATH = {
    "nba": "players, games, teams, intelligence, features, public",
    "mlb": "mlb_players, mlb_games, mlb_teams, mlb_intelligence, mlb_features, public",
}
# identifiers a query leaves for the code to fill from a fixed list
IDENTIFIERS = {"{table}": "batter_game_logs", "{stat_col}": "hits"}
FILES = [(pkg, name) for pkg, mod in PACKAGES.items() for name in mod.names()]


@pytest.fixture(scope="module")
def conn():
    if not os.environ.get("DB_PASSWORD"):
        pytest.skip("no database settings in the environment")
    psycopg2 = pytest.importorskip("psycopg2")
    try:
        connection = psycopg2.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            port=int(os.environ.get("DB_PORT", "5500")),
            dbname=os.environ.get("DB_NAME", "sportsuite"),
            user=os.environ.get("DB_USER", "postgres"),
            password=os.environ["DB_PASSWORD"],
            connect_timeout=3,
        )
    except psycopg2.OperationalError as exc:
        pytest.skip(f"no database reachable: {str(exc).splitlines()[0]}")
    yield connection
    connection.close()


def plannable(text: str):
    """(statement, arguments) with every placeholder bound to NULL."""
    body = "\n".join(line for line in text.split("\n") if not line.startswith("--"))
    for placeholder, identifier in IDENTIFIERS.items():
        body = body.replace(placeholder, identifier)
    rows = re.search(r"INSERT INTO [\w.]+\s*\(([^)]*)\)\s*VALUES %s", body, re.S)
    if rows:  # an execute_values statement: one row of NULLs
        width = len([c for c in rows.group(1).split(",") if c.strip()])
        body = body.replace("VALUES %s", "VALUES (" + ", ".join(["NULL"] * width) + ")")
    named = set(re.findall(r"%\((\w+)\)s", body))
    args = {key: None for key in named} if named else tuple([None] * body.count("%s"))
    return body, args


def test_placeholders_bind():
    body, args = plannable("-- x.\nSELECT 1 FROM {table} WHERE a = %s AND b = %s;")
    assert body == "SELECT 1 FROM batter_game_logs WHERE a = %s AND b = %s;" and args == (
        None,
        None,
    )
    body, args = plannable("INSERT INTO t (a, b, c)\nVALUES %s\nON CONFLICT DO NOTHING;")
    assert "VALUES (NULL, NULL, NULL)" in body and args == ()
    assert plannable("SELECT %(curr)s, %(curr)s, %(prev)s;")[1] == {"curr": None, "prev": None}


@pytest.mark.parametrize("pkg,name", FILES)
def test_the_database_can_plan_the_query(conn, pkg, name):
    body, args = plannable(PACKAGES[pkg].named_query(name))
    with conn.cursor() as cur:
        try:
            cur.execute(f"SET LOCAL search_path TO {SEARCH_PATH[pkg]}")
            cur.execute("EXPLAIN " + body, args)
            assert cur.fetchone()
        finally:
            conn.rollback()
