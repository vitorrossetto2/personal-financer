import unittest

import app as novosaldo


class AmountParsingTests(unittest.TestCase):
    def test_account_balance_accepts_negative_brazilian_value(self):
        self.assertEqual(
            novosaldo.parse_amount("-433,7", require_positive=False),
            -433.70,
        )

    def test_account_balance_accepts_zero(self):
        self.assertEqual(novosaldo.parse_amount("0", require_positive=False), 0.0)

    def test_transaction_amount_still_rejects_negative_value(self):
        with self.assertRaises(ValueError):
            novosaldo.parse_amount("-433,7")


if __name__ == "__main__":
    unittest.main()
