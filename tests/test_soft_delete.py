import sqlite3
import tempfile
import unittest
from pathlib import Path

import app as novosaldo


class SoftDeleteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        novosaldo.DATABASE = Path(self.directory.name) / "test.db"
        novosaldo.app.config.update(TESTING=True, SECRET_KEY="test", AUTH_DISABLED_FOR_TESTS=True)
        self.client = novosaldo.app.test_client()
        self.client.get("/")
        connection = sqlite3.connect(novosaldo.DATABASE)
        try:
            cursor = connection.execute(
                """INSERT INTO transactions
                   (kind, description, amount, due_date, created_at)
                   VALUES ('expense', 'Bloco Tati', 100, '2026-09-14', '2026-09-14T12:00:00')"""
            )
            self.transaction_id = cursor.lastrowid
            connection.commit()
        finally:
            connection.close()

    def tearDown(self):
        self.directory.cleanup()

    def test_delete_hides_transaction_and_restore_brings_it_back(self):
        response = self.client.post(
            f"/lancamentos/{self.transaction_id}/excluir", follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Bloco Tati", response.get_data(as_text=True))

        deleted_page = self.client.get("/lancamentos/excluidos")
        self.assertIn("Bloco Tati", deleted_page.get_data(as_text=True))

        response = self.client.post(
            f"/lancamentos/{self.transaction_id}/restaurar", follow_redirects=True
        )
        self.assertNotIn("Bloco Tati", response.get_data(as_text=True))
        active_page = self.client.get("/lancamentos/expense")
        self.assertIn("Bloco Tati", active_page.get_data(as_text=True))

    def test_deleted_transaction_is_removed_from_dashboard_totals(self):
        before = self.client.get("/?month=2026-09").get_data(as_text=True)
        self.assertIn("Bloco Tati", before)

        self.client.post(f"/lancamentos/{self.transaction_id}/excluir")
        after = self.client.get("/?month=2026-09").get_data(as_text=True)
        self.assertNotIn("Bloco Tati", after)


if __name__ == "__main__":
    unittest.main()
