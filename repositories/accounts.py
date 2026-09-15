from __future__ import annotations

import sqlite3


class AccountRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def list_all(self):
        return self.connection.execute("SELECT * FROM accounts ORDER BY id").fetchall()

    def find(self, account_id: int):
        return self.connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()

    def total_balance(self) -> float:
        return self.connection.execute("SELECT COALESCE(SUM(balance), 0) FROM accounts").fetchone()[0]

    def update_balance(self, account_id: int, balance: float, updated_at: str) -> bool:
        cursor = self.connection.execute(
            "UPDATE accounts SET balance = ?, updated_at = ? WHERE id = ?",
            (balance, updated_at, account_id),
        )
        return cursor.rowcount > 0
