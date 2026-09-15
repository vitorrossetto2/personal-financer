import sqlite3
import tempfile
import unittest
from pathlib import Path

import app as novosaldo


class DashboardMonthNavigationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        novosaldo.DATABASE = Path(self.directory.name) / "test.db"
        novosaldo.app.config.update(TESTING=True, SECRET_KEY="test", AUTH_DISABLED_FOR_TESTS=True)
        self.client = novosaldo.app.test_client()
        self.client.get("/")
        connection = sqlite3.connect(novosaldo.DATABASE)
        try:
            connection.execute(
                "UPDATE accounts SET balance = 1000 WHERE id = (SELECT MIN(id) FROM accounts)"
            )
            connection.executemany(
                """INSERT INTO transactions
                   (kind, description, amount, due_date, created_at)
                   VALUES (?, ?, ?, ?, '2026-09-14T12:00:00')""",
                [
                    ("expense", "Despesa de setembro", 100, "2026-09-14"),
                    ("income", "Receita de setembro", 500, "2026-09-20"),
                    ("expense", "Despesa de outubro", 100, "2026-10-14"),
                    ("income", "Receita de outubro", 200, "2026-10-20"),
                ],
            )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self):
        self.directory.cleanup()

    def test_can_open_next_month_and_only_see_its_transactions(self):
        response = self.client.get("/?month=2026-10")
        page = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Despesa de outubro", page)
        self.assertNotIn("Despesa de setembro", page)
        self.assertIn("Próximo mês", page)
        self.assertIn("/?month=2026-11", page)
        self.assertIn("/?month=2026-09", page)
        self.assertIn("Saldo do fim do mês anterior", page)
        self.assertIn("R$ 1.400,00", page)
        self.assertIn("R$ 1.500,00", page)

    def test_invalid_month_redirects_to_current_month(self):
        response = self.client.get("/?month=invalid")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_card_entries_are_consolidated_outside_the_card_page(self):
        connection = sqlite3.connect(novosaldo.DATABASE)
        try:
            cursor = connection.execute(
                """INSERT INTO credit_cards
                   (name, issuer, last_four, payment_day, created_at, updated_at)
                   VALUES ('Cartão Teste', 'itau', '1234', 10,
                           '2026-09-14T12:00:00', '2026-09-14T12:00:00')"""
            )
            card_id = cursor.lastrowid
            connection.executemany(
                """INSERT INTO transactions
                   (kind, description, amount, due_date, credit_card_id, created_at)
                   VALUES ('expense', ?, ?, '2026-10-10', ?, '2026-09-14T12:00:00')""",
                [("Compra secreta A", 30, card_id), ("Compra secreta B", 70, card_id)],
            )
            connection.commit()
        finally:
            connection.close()

        for path in ("/?month=2026-10", "/lancamentos/expense"):
            page = self.client.get(path).get_data(as_text=True)
            self.assertNotIn("Compra secreta A", page)
            self.assertNotIn("Compra secreta B", page)
            self.assertEqual(page.count("Fatura Cartão Teste"), 1)
            self.assertIn("Ver fatura", page)

        card_page = self.client.get(
            f"/cartoes?card_id={card_id}&month=2026-10"
        ).get_data(as_text=True)
        self.assertIn("Compra secreta A", card_page)
        self.assertIn("Compra secreta B", card_page)

    def test_can_settle_and_reopen_a_consolidated_card_invoice(self):
        connection = sqlite3.connect(novosaldo.DATABASE)
        try:
            cursor = connection.execute(
                """INSERT INTO credit_cards
                   (name, issuer, last_four, payment_day, created_at, updated_at)
                   VALUES ('Cartão Teste', 'itau', '1234', 10,
                           '2026-09-14T12:00:00', '2026-09-14T12:00:00')"""
            )
            card_id = cursor.lastrowid
            connection.executemany(
                """INSERT INTO transactions
                   (kind, description, amount, due_date, credit_card_id, status, created_at)
                   VALUES ('expense', ?, ?, '2026-10-10', ?, ?, '2026-09-14T12:00:00')""",
                [
                    ("Compra pendente", 30, card_id, "pending"),
                    ("Compra paga", 70, card_id, "settled"),
                ],
            )
            connection.commit()
        finally:
            connection.close()

        page = self.client.get("/lancamentos/expense").get_data(as_text=True)
        self.assertIn(f'/cartoes/{card_id}/faturas/2026-10/baixar', page)
        self.assertIn("Dar baixa", page)

        response = self.client.post(
            f"/cartoes/{card_id}/faturas/2026-10/baixar", follow_redirects=True
        )
        self.assertIn("Fatura baixada.", response.get_data(as_text=True))
        connection = sqlite3.connect(novosaldo.DATABASE)
        try:
            rows = connection.execute(
                """SELECT status, settled_date FROM transactions
                   WHERE credit_card_id = ? ORDER BY id""",
                (card_id,),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual([row[0] for row in rows], ["settled", "settled"])
        self.assertTrue(all(row[1] for row in rows))

        response = self.client.post(
            f"/cartoes/{card_id}/faturas/2026-10/baixar", follow_redirects=True
        )
        self.assertIn("Baixa da fatura desfeita.", response.get_data(as_text=True))
        connection = sqlite3.connect(novosaldo.DATABASE)
        try:
            rows = connection.execute(
                """SELECT status, settled_date FROM transactions
                   WHERE credit_card_id = ? ORDER BY id""",
                (card_id,),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual([row[0] for row in rows], ["pending", "pending"])
        self.assertTrue(all(row[1] is None for row in rows))

    def test_invoice_settlement_does_not_change_other_months_or_deleted_entries(self):
        connection = sqlite3.connect(novosaldo.DATABASE)
        try:
            cursor = connection.execute(
                """INSERT INTO credit_cards
                   (name, issuer, last_four, payment_day, created_at, updated_at)
                   VALUES ('Cartão Teste', 'itau', '1234', 10,
                           '2026-09-14T12:00:00', '2026-09-14T12:00:00')"""
            )
            card_id = cursor.lastrowid
            connection.executemany(
                """INSERT INTO transactions
                   (kind, description, amount, due_date, credit_card_id, deleted_at, created_at)
                   VALUES ('expense', ?, 10, ?, ?, ?, '2026-09-14T12:00:00')""",
                [
                    ("Fatura alvo", "2026-10-10", card_id, None),
                    ("Outro mês", "2026-11-10", card_id, None),
                    ("Excluída", "2026-10-10", card_id, "2026-09-15T10:00:00"),
                ],
            )
            connection.commit()
        finally:
            connection.close()

        self.client.post(f"/cartoes/{card_id}/faturas/2026-10/baixar")
        connection = sqlite3.connect(novosaldo.DATABASE)
        try:
            statuses = dict(connection.execute(
                "SELECT description, status FROM transactions WHERE credit_card_id = ?",
                (card_id,),
            ).fetchall())
        finally:
            connection.close()
        self.assertEqual(statuses["Fatura alvo"], "settled")
        self.assertEqual(statuses["Outro mês"], "pending")
        self.assertEqual(statuses["Excluída"], "pending")


if __name__ == "__main__":
    unittest.main()
