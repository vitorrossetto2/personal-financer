from __future__ import annotations

import sqlite3


class RecurrenceRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(self, *, kind: str, description: str, category: str, amount: float,
               day_of_month: int, start_date: str, account_id=None) -> int:
        cursor = self.connection.execute(
            """INSERT INTO recurrences
               (kind, description, category, amount, day_of_month, start_date, account_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (kind, description, category, amount, day_of_month, start_date, account_id),
        )
        return cursor.lastrowid

    def list_by_kind(self, kind: str):
        return self.connection.execute(
            "SELECT * FROM recurrences WHERE kind = ? ORDER BY active DESC, description", (kind,)
        ).fetchall()

    def list_active(self):
        return self.connection.execute("SELECT * FROM recurrences WHERE active = 1").fetchall()

    def list_active_for_period(self, first_day: str, last_day: str):
        return self.connection.execute(
            """SELECT * FROM recurrences WHERE active = 1 AND start_date <= ?
               AND (end_date IS NULL OR end_date >= ?)""",
            (last_day, first_day),
        ).fetchall()

    def toggle(self, recurrence_id: int) -> bool:
        cursor = self.connection.execute(
            "UPDATE recurrences SET active = CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id = ?",
            (recurrence_id,),
        )
        return cursor.rowcount > 0
