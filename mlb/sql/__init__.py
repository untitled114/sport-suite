"""Named SQL queries for the MLB side, one file per query.

Each ``<name>.sql`` beside this module opens with a comment header (what the query answers,
its parameters in order, and the code that runs it). ``named_query("<name>")`` returns the file's
text, ready for ``cursor.execute(named_query(...), params)``.
"""

from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def named_query(name: str) -> str:
    """The text of the query file ``<name>.sql`` in this folder."""
    path = _HERE / f"{name}.sql"
    if not path.is_file():
        raise FileNotFoundError(f"no query named {name!r} in {_HERE}")
    return path.read_text(encoding="utf-8")


def names() -> list:
    """Every query name in this folder, sorted."""
    return sorted(p.stem for p in _HERE.glob("*.sql"))
