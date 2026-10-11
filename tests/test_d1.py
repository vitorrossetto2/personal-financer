import io
import sqlite3
import unittest
from datetime import date
from pathlib import Path

from d1_database import D1Connection
from services import CreditCardService, FinanceService, parse_credit_card_statement
from test_credit_card_import import statement_bytes, future_statement_bytes, parallel_installments_statement_bytes


class Statement:
    def __init__(self, binding, sql, parameters=()):
        self.binding, self.sql, self.parameters = binding, sql, parameters

    def bind(self, *parameters):
        return Statement(self.binding, self.sql, parameters)

    def all(self):
        def execute():
            cursor = self.binding.db.execute(self.sql, self.parameters)
            result = {"success": True, "results": [dict(row) for row in cursor.fetchall()],
                      "meta": {"changes": max(cursor.rowcount, 0), "last_row_id": cursor.lastrowid}}
            if not self.binding.in_batch:
                self.binding.db.commit()
            return result
        return execute


class Binding:
    """D1 contract double backed by real SQLite, including atomic batch rollback."""
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        migration = Path(__file__).resolve().parents[1] / "cloudflare/migrations/0001_initial.sql"
        self.db.executescript(migration.read_text(encoding="utf-8"))
        self.in_batch = False
        self.batches = 0
        self.fail_at = None

    def prepare(self, sql):
        return Statement(self, sql)

    def batch(self, statements):
        def execute():
            self.batches += 1
            self.in_batch = True
            try:
                with self.db:
                    results = []
                    for index, statement in enumerate(statements):
                        if index == self.fail_at:
                            raise RuntimeError("Simulated D1 failure")
                        results.append(statement.all()())
                    return results
            finally:
                self.in_batch = False
        return execute


class D1Tests(unittest.TestCase):
    def setUp(self):
        self.binding = Binding()
        self.connection = D1Connection(self.binding, run_sync=lambda operation: operation())
        self.cards = CreditCardService(self.connection)
        self.card_id = self.cards.create_card(name="Test", issuer="itau", last_four="2788", payment_day=5)

    def tearDown(self):
        self.binding.db.close()

    def save(self, content):
        statement = parse_credit_card_statement(io.BytesIO(content), "itau")
        return self.cards.save_statement(statement, "test.xlsx", credit_card_id=self.card_id)

    def test_import_is_atomic_incremental_and_confirms_future_installments(self):
        result = self.save(statement_bytes())
        self.assertEqual((result["imported"], result["scheduled"]), (2, 3))
        self.assertEqual(self.binding.batches, 1)
        result = self.save(statement_bytes(include_new_purchase=True))
        self.assertEqual((result["imported"], result["duplicates"]), (1, 2))
        result = self.save(future_statement_bytes())
        self.assertEqual(result["confirmed"], 1)
        row = self.connection.execute("SELECT * FROM transactions WHERE installment_number = 3").fetchone()
        self.assertEqual(row["amount"], 55)
        self.assertEqual(row["card_installment_projected"], 0)
        self.assertEqual(row["due_date"], "2026-10-05")
        self.assertEqual(self.save(future_statement_bytes())["imported"], 0)

    def test_parallel_installments_remain_separate(self):
        self.assertEqual(self.save(parallel_installments_statement_bytes())["imported"], 2)
        self.assertEqual(self.save(parallel_installments_statement_bytes())["duplicates"], 2)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 4)

    def test_import_failure_rolls_back_every_write(self):
        self.binding.fail_at = 1
        with self.assertRaisesRegex(RuntimeError, "Simulated"):
            self.save(statement_bytes())
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 0)
        self.binding.fail_at = None
        self.assertEqual(self.save(statement_bytes())["imported"], 2)

    def test_deleted_import_is_not_recreated(self):
        self.save(statement_bytes())
        self.connection.execute("UPDATE transactions SET deleted_at = '2026-09-15' WHERE description = 'Mercado'")
        self.assertEqual(self.save(statement_bytes())["imported"], 0)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM transactions WHERE deleted_at IS NOT NULL").fetchone()[0], 1)

    def test_legacy_key_is_migrated(self):
        self.save(statement_bytes())
        self.connection.execute("UPDATE transactions SET import_key = 'legacy' WHERE installment_number = 3")
        result = self.save(future_statement_bytes())
        self.assertEqual(result["confirmed"], 1)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 5)

    def test_installments_are_one_batch_and_preserve_total(self):
        result = FinanceService(self.connection).create_installments(
            kind="expense", description="Test", category="", total_amount=100,
            first_due_date=date(2026, 1, 31), count=3,
        )
        self.assertEqual(len(set(result["transaction_ids"])), 3)
        self.assertEqual(self.binding.batches, 1)
        rows = self.connection.execute("SELECT * FROM transactions ORDER BY id").fetchall()
        self.assertEqual([row["due_date"] for row in rows], ["2026-01-31", "2026-02-28", "2026-03-31"])
        self.assertAlmostEqual(sum(row["amount"] for row in rows), 100)

    def test_installment_failure_rolls_back(self):
        self.binding.fail_at = 1
        with self.assertRaises(RuntimeError):
            FinanceService(self.connection).create_installments(
                kind="expense", description="Test", category="", total_amount=100,
                first_due_date=date(2026, 1, 31), count=3,
            )
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 0)

    def test_constraint_errors_keep_existing_app_error_handling(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.cards.create_card(name="Test", issuer="itau", last_four="2788", payment_day=5)
