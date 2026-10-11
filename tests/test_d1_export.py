import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import connect, initialize
from scripts.export_d1 import export_database


class D1ExportTests(unittest.TestCase):
    def test_export_preserves_rows_and_source_and_can_be_followed_by_migrations(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "local.db"
            output = Path(folder) / "export.sql"
            connection = connect(source)
            initialize(connection)
            connection.execute("UPDATE accounts SET balance = 123.45 WHERE id = 1")
            connection.execute("""INSERT INTO transactions
                (kind, description, amount, due_date, created_at, deleted_at, import_key)
                VALUES ('expense', 'Apostrophe '' and unicode: café', 25, '2026-09-15',
                        '2026-09-15', '2026-09-16', 'stable-key')""")
            connection.commit()
            connection.close()
            original = source.read_bytes()
            export_database(source, output)
            self.assertEqual(source.read_bytes(), original)
            destination = sqlite3.connect(":memory:")
            try:
                destination.execute("PRAGMA foreign_keys = ON")
                destination.executescript(output.read_text(encoding="utf-8"))
                migration = Path(__file__).resolve().parents[1] / "cloudflare/migrations/0001_initial.sql"
                destination.executescript(migration.read_text(encoding="utf-8"))
                self.assertEqual(destination.execute("SELECT balance FROM accounts WHERE id=1").fetchone()[0], 123.45)
                self.assertEqual(destination.execute("SELECT import_key, deleted_at FROM transactions").fetchone(),
                                 ("stable-key", "2026-09-16"))
                self.assertEqual(destination.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 2)
                with self.assertRaises(FileExistsError):
                    export_database(source, output)
            finally:
                destination.close()
