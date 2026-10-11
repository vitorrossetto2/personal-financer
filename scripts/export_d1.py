"""Export a local SQLite snapshot for importing into an EMPTY D1 database."""
import argparse
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from database import initialize


def export_database(source: Path, destination: Path):
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if source == destination:
        raise ValueError("The SQL output must not overwrite the source database")
    local = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    snapshot = sqlite3.connect(":memory:")
    try:
        local.backup(snapshot)
        initialize(snapshot)
        statements = [
            statement for statement in snapshot.iterdump()
            if statement not in {"BEGIN TRANSACTION;", "COMMIT;"}
            and '"sqlite_sequence"' not in statement
        ]
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects an existing export from accidental overwrite.
        with destination.open("x", encoding="utf-8") as output:
            output.write("-- Import into an empty D1 database BEFORE applying migrations.\n")
            output.write("PRAGMA defer_foreign_keys = ON;\n")
            output.write("\n".join(statements) + "\n")
    finally:
        snapshot.close()
        local.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "instance" / "financeiro.db")
    parser.add_argument("--output", type=Path, default=ROOT / "instance" / "d1-export.sql")
    args = parser.parse_args()
    export_database(args.source, args.output)
    print(f"Exported to {args.output}. The source database was not changed.")
