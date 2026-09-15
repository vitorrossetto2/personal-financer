from __future__ import annotations

import sqlite3


class CreditCardRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(self, *, name: str, issuer: str, last_four: str, payment_day: int,
               timestamp: str) -> int:
        cursor = self.connection.execute(
            """INSERT INTO credit_cards
               (name, issuer, last_four, payment_day, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, issuer, last_four, payment_day, timestamp, timestamp),
        )
        return cursor.lastrowid

    def list_all(self):
        return self.connection.execute(
            """SELECT * FROM credit_cards
               WHERE active = 1
               ORDER BY name COLLATE NOCASE"""
        ).fetchall()

    def find_active(self, card_id: int):
        return self.connection.execute(
            "SELECT * FROM credit_cards WHERE id = ? AND active = 1", (card_id,)
        ).fetchone()
