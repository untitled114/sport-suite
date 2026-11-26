"""The named query files (nba/sql, mlb/sql): each one is documented, and the code and the files
agree on the names."""

import re
from pathlib import Path

import pytest

import mlb.sql
import nba.sql

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = {"nba": nba.sql, "mlb": mlb.sql}
CALL = re.compile(r"from (nba|mlb)\.sql import named_query")
USE = re.compile(r'named_query\(\s*"([a-z0-9_]+)"\s*\)')


def _uses():
    """{(package, query name): [files that run it]} from every Python file that imports a loader."""
    found = {}
    for top in ("nba", "mlb", "scripts", "airflow"):
        for path in (ROOT / top).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            imported = CALL.search(text)
            if not imported:
                continue
            for name in USE.findall(text):
                found.setdefault((imported.group(1), name), []).append(
                    path.relative_to(ROOT).as_posix()
                )
    return found


FILES = [(pkg, name) for pkg, mod in PACKAGES.items() for name in mod.names()]


def test_there_are_query_files():
    assert len(FILES) >= 37


@pytest.mark.parametrize("pkg,name", FILES)
def test_a_query_file_is_documented_and_is_one_statement(pkg, name):
    text = PACKAGES[pkg].named_query(name)
    lines = text.split("\n")
    header = []
    for line in lines:
        if not line.startswith("--"):
            break
        header.append(line)
    # comments inside the statement do not count: a ";" in one must not pass for the terminator
    body = "\n".join(line.split("--", 1)[0].rstrip() for line in lines[len(header) :]).strip()
    assert header[0].startswith("-- ") and header[0].endswith(
        "."
    ), "first line says what it answers"
    assert any(h.startswith("-- Parameters") for h in header)
    run_by = [h for h in header if h.startswith("-- Run by: ")]
    assert len(run_by) == 1
    assert (ROOT / run_by[0].split(": ", 1)[1].split(" ")[0]).is_file()
    assert body.endswith(";") and body.count(";") == 1, "one statement per file"
    assert all(len(line) <= 100 for line in lines)


@pytest.mark.parametrize("pkg,name", FILES)
def test_a_query_file_is_run_by_the_code_its_header_names(pkg, name):
    header = PACKAGES[pkg].named_query(name)
    assert (pkg, name) in USES, f"{pkg}/sql/{name}.sql is not used by any code"
    assert any(f"-- Run by: {user}" in header for user in USES[(pkg, name)])


def test_every_name_the_code_asks_for_exists():
    assert USES, "no code uses a named query"
    missing = [key for key in USES if key not in set(FILES)]
    assert missing == []


def test_an_unknown_name_fails_loudly():
    with pytest.raises(FileNotFoundError, match="no query named 'nope'"):
        nba.sql.named_query("nope")


USES = _uses()
