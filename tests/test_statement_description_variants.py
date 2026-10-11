import copy
import io
import sqlite3
import unittest

from database import SCHEMA
from services.credit_card import CreditCardService, parse_itau_statement
from test_credit_card_import import statement_bytes, parallel_installments_statement_bytes
from repositories.card_identity import equivalent_description


class DescriptionVariantTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.execute("INSERT INTO credit_cards (name,issuer,last_four,payment_day,created_at,updated_at) VALUES ('Itau','itau','2788',5,'now','now')")
        self.service = CreditCardService(self.db)

    def tearDown(self):
        self.db.close()

    def test_paid_variants_reimport_and_backwards_preserve_occurrences(self):
        statement = parse_itau_statement(io.BytesIO(parallel_installments_statement_bytes()))
        # Include two genuinely identical purchases as separate occurrences.
        statement['entries'][1]['amount'] = 80
        self.service.save_statement(statement, 'open.xlsx', credit_card_id=1)
        paid = copy.deepcopy(statement)
        for entry in paid['entries']:
            entry['description'] = f"{entry['description']:<18}{'Sao Paulo':<14}Bra"
        result = self.service.save_statement(paid, 'paid.xlsx', credit_card_id=1)
        self.assertEqual((result['imported'], result['duplicates'], result['scheduled']), (0, 2, 0))
        self.assertEqual(self.service.save_statement(paid, 'again.xlsx', credit_card_id=1)['imported'], 0)
        self.assertEqual(self.service.save_statement(statement, 'open.xlsx', credit_card_id=1)['imported'], 0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM transactions').fetchone()[0], 4)
        paid['due_date'] = '2026-10-01'
        for entry in paid['entries']:
            entry['installment_number'] = 3
            entry['amount'] = 85
        result = self.service.save_statement(paid, 'next.xlsx', credit_card_id=1)
        self.assertEqual((result['confirmed'], result['imported']), (2, 0))

    def test_paid_variants_add_only_new_purchase(self):
        statement = parse_itau_statement(io.BytesIO(statement_bytes()))
        self.service.save_statement(statement, 'open.xlsx', credit_card_id=1)
        paid = parse_itau_statement(io.BytesIO(statement_bytes(True)))
        for entry in paid['entries']:
            width = 18 if entry['installment_number'] else 23
            entry['description'] = f"{entry['description']:<{width}}{'Balneario Cam':<14}Sc"
        result = self.service.save_statement(paid, 'paid.xlsx', credit_card_id=1)
        self.assertEqual((result['imported'], result['duplicates'], result['scheduled']), (1, 2, 0))

    def test_different_merchants_remain_distinct(self):
        self.assertFalse(equivalent_description('Loja', 'Loja Shopping'))
        self.assertTrue(equivalent_description('Graciola Eventos   Sab', 'Graciola Eventos   Sab Itajai        Sc'))
        self.assertTrue(equivalent_description('Shopee *Haishoplam', 'Shopee *Haishoplamsao Paulo     Bra'))
