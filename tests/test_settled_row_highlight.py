import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

import app as novosaldo


class SettledRowHighlightTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        novosaldo.DATABASE = Path(self.directory.name) / "test.db"
        novosaldo.app.config.update(
            TESTING=True, SECRET_KEY="test", AUTH_DISABLED_FOR_TESTS=True
        )
        self.client = novosaldo.app.test_client()
        self.client.get("/")

    def tearDown(self):
        self.directory.cleanup()

    def insert_transaction(self, *, description, credit_card_id=None):
        connection = sqlite3.connect(novosaldo.DATABASE)
        try:
            cursor = connection.execute(
                """INSERT INTO transactions
                   (kind, description, amount, due_date, purchase_date,
                    credit_card_id, created_at)
                   VALUES ('expense', ?, 100, '2026-09-15', '2026-09-01', ?,
                           '2026-09-15T12:00:00')""",
                (description, credit_card_id),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    def assert_description_is_in_settled_row(self, page, description):
        self.assertRegex(
            page,
            rf'(?s)<tr class="settled-row">.*?{re.escape(description)}.*?</tr>',
        )

    def test_settled_transaction_row_is_green_in_shared_tables(self):
        transaction_id = self.insert_transaction(description="Despesa quitada")

        self.client.post(f"/lancamentos/{transaction_id}/baixar")

        for path in ("/?month=2026-09", "/lancamentos/expense"):
            page = self.client.get(path).get_data(as_text=True)
            self.assert_description_is_in_settled_row(page, "Despesa quitada")

    def test_settled_transaction_row_is_green_on_credit_card_page(self):
        connection = sqlite3.connect(novosaldo.DATABASE)
        try:
            cursor = connection.execute(
                """INSERT INTO credit_cards
                   (name, issuer, last_four, payment_day, created_at, updated_at)
                   VALUES ('Cartao Verde', 'itau', '1234', 15,
                           '2026-09-15T12:00:00', '2026-09-15T12:00:00')"""
            )
            card_id = cursor.lastrowid
            connection.commit()
        finally:
            connection.close()
        transaction_id = self.insert_transaction(
            description="Compra quitada", credit_card_id=card_id
        )

        self.client.post(f"/lancamentos/{transaction_id}/baixar")

        page = self.client.get(
            f"/cartoes?card_id={card_id}&month=2026-09"
        ).get_data(as_text=True)
        self.assert_description_is_in_settled_row(page, "Compra quitada")


if __name__ == "__main__":
    unittest.main()
