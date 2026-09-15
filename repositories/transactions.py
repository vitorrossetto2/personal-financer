from __future__ import annotations

import sqlite3


class TransactionRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(self, *, kind: str, description: str, category: str, amount: float,
               due_date: str, created_at: str, account_id=None, recurrence_id=None,
               installment_group=None, installment_number=None, installment_total=None,
               purchase_date=None, credit_card_id=None) -> int:
        cursor = self.connection.execute(
            """INSERT INTO transactions
               (kind, description, category, amount, due_date, account_id, recurrence_id,
                installment_group, installment_number, installment_total, purchase_date,
                credit_card_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (kind, description, category, amount, due_date, account_id, recurrence_id,
             installment_group, installment_number, installment_total, purchase_date,
             credit_card_id, created_at),
        )
        return cursor.lastrowid

    def create_from_recurrence(self, rule, due_date: str, created_at: str) -> bool:
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO transactions
               (kind, description, category, amount, due_date, account_id, recurrence_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (rule["kind"], rule["description"], rule["category"], rule["amount"], due_date,
             rule["account_id"], rule["id"], created_at),
        )
        return cursor.rowcount > 0

    def import_card_entry(self, entry: dict, *, credit_card_id: int, due_date: str, category: str,
                          filename: str, created_at: str, projected: bool = False) -> str:
        existing = self.connection.execute(
            "SELECT * FROM transactions WHERE import_key = ?",
            (entry["import_key"],),
        ).fetchone()
        if existing and existing["deleted_at"] is not None:
            return "duplicate"
        if not existing:
            existing = self._find_equivalent_card_entry(
                entry, credit_card_id=credit_card_id, due_date=due_date
            )
        if existing:
            if not projected and existing["card_installment_projected"]:
                self._confirm_projected_entry(
                    existing["id"], entry, due_date=due_date, category=category,
                    filename=filename,
                )
                return "confirmed"
            if existing["import_key"] != entry["import_key"]:
                self.connection.execute(
                    "UPDATE transactions SET import_key = ? WHERE id = ?",
                    (entry["import_key"], existing["id"]),
                )
            return "duplicate"

        cursor = self.connection.execute(
            """INSERT INTO transactions
               (kind, description, category, amount, due_date, installment_group,
                installment_number, installment_total, purchase_date, import_source,
                import_key, credit_card_id, cardholder_name, card_last_four,
                card_installment_projected, created_at)
               VALUES ('expense', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entry["description"], category, entry["amount"], due_date,
             entry["installment_group"], entry["installment_number"], entry["installment_total"],
             entry["purchase_date"], filename, entry["import_key"], credit_card_id,
             entry.get("cardholder_name"), entry.get("card_last_four"),
             1 if projected else 0, created_at),
        )
        if cursor.rowcount > 0:
            return "inserted"

    def _find_equivalent_card_entry(self, entry: dict, *, credit_card_id: int, due_date: str):
        """Localiza a ocorrência equivalente criada por versões antigas da chave de importação."""
        parameters = [
            credit_card_id, due_date, entry["purchase_date"], entry["description"],
            entry.get("installment_number"), entry.get("installment_number"),
            entry.get("installment_total"), entry.get("installment_total"),
            entry.get("cardholder_name") or "", entry.get("card_last_four") or "",
        ]
        amount_filter = ""
        if not entry.get("installment_number"):
            amount_filter = " AND amount = ?"
            parameters.append(entry["amount"])
        parameters.append(max(int(entry.get("occurrence") or 1) - 1, 0))
        return self.connection.execute(
            f"""SELECT * FROM transactions
                WHERE credit_card_id = ? AND due_date = ? AND purchase_date = ?
                  AND description = ?
                  AND (installment_number = ? OR (installment_number IS NULL AND ? IS NULL))
                  AND (installment_total = ? OR (installment_total IS NULL AND ? IS NULL))
                  AND COALESCE(cardholder_name, '') = ?
                  AND COALESCE(card_last_four, '') = ?
                  AND deleted_at IS NULL{amount_filter}
                ORDER BY id
                LIMIT 1 OFFSET ?""",
            parameters,
        ).fetchone()

    def _confirm_projected_entry(self, transaction_id: int, entry: dict, *, due_date: str,
                                 category: str, filename: str) -> None:
        self.connection.execute(
            """UPDATE transactions
               SET description = ?, category = ?, amount = ?, due_date = ?,
                   installment_group = ?, installment_number = ?, installment_total = ?,
                   purchase_date = ?, import_source = ?, import_key = ?, cardholder_name = ?,
                   card_last_four = ?, card_installment_projected = 0
               WHERE id = ?""",
            (entry["description"], category, entry["amount"], due_date,
             entry["installment_group"], entry["installment_number"], entry["installment_total"],
             entry["purchase_date"], filename, entry["import_key"],
             entry.get("cardholder_name"), entry.get("card_last_four"), transaction_id),
        )

    def list_for_credit_card_period(self, card_id: int, start: str, end: str, today: str):
        return self.connection.execute(
            """SELECT t.*,
               CASE WHEN t.status = 'pending' AND t.due_date < ? THEN 1 ELSE 0 END overdue
               FROM transactions t
               WHERE t.credit_card_id = ? AND t.due_date BETWEEN ? AND ?
                 AND t.deleted_at IS NULL
               ORDER BY t.purchase_date DESC, t.id DESC""",
            (today, card_id, start, end),
        ).fetchall()

    def list_for_kind(self, kind: str, today: str):
        return self.connection.execute(
            """SELECT t.*, c.name credit_card_name,
               CASE WHEN t.status = 'pending' AND t.due_date < ? THEN 1 ELSE 0 END overdue
               FROM transactions t
               LEFT JOIN credit_cards c ON c.id = t.credit_card_id
               WHERE t.kind = ? AND t.deleted_at IS NULL
               ORDER BY t.status, t.due_date DESC, t.id DESC""",
            (today, kind),
        ).fetchall()

    def list_for_period(self, start: str, end: str, today: str):
        return self.connection.execute(
            """SELECT t.*, c.name credit_card_name,
               CASE WHEN t.status = 'pending' AND t.due_date < ? THEN 1 ELSE 0 END overdue
               FROM transactions t
               LEFT JOIN credit_cards c ON c.id = t.credit_card_id
               WHERE t.due_date BETWEEN ? AND ? AND t.deleted_at IS NULL
               ORDER BY t.due_date, t.kind DESC""",
            (today, start, end),
        ).fetchall()

    def pending_totals(self, start: str, end: str):
        return self.connection.execute(
            """SELECT kind, COALESCE(SUM(amount), 0) total FROM transactions
               WHERE due_date BETWEEN ? AND ? AND status = 'pending' AND deleted_at IS NULL
               GROUP BY kind""",
            (start, end),
        ).fetchall()

    def find_active(self, transaction_id: int):
        return self.connection.execute(
            "SELECT * FROM transactions WHERE id = ? AND deleted_at IS NULL", (transaction_id,)
        ).fetchone()

    def find_deleted(self, transaction_id: int):
        return self.connection.execute(
            "SELECT * FROM transactions WHERE id = ? AND deleted_at IS NOT NULL", (transaction_id,)
        ).fetchone()

    def update_status(self, transaction_id: int, status: str, settled_date: str | None) -> None:
        self.connection.execute(
            "UPDATE transactions SET status = ?, settled_date = ? WHERE id = ?",
            (status, settled_date, transaction_id),
        )

    def credit_card_invoice_status_counts(self, card_id: int, start: str, end: str):
        return self.connection.execute(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) pending
               FROM transactions
               WHERE credit_card_id = ? AND due_date BETWEEN ? AND ?
                 AND deleted_at IS NULL""",
            (card_id, start, end),
        ).fetchone()

    def update_credit_card_invoice_status(
        self, card_id: int, start: str, end: str, status: str, settled_date: str | None
    ) -> int:
        cursor = self.connection.execute(
            """UPDATE transactions
               SET status = ?, settled_date = ?
               WHERE credit_card_id = ? AND due_date BETWEEN ? AND ?
                 AND deleted_at IS NULL""",
            (status, settled_date, card_id, start, end),
        )
        return cursor.rowcount

    def soft_delete(self, transaction_id: int, deleted_at: str) -> None:
        self.connection.execute(
            "UPDATE transactions SET deleted_at = ? WHERE id = ?", (deleted_at, transaction_id)
        )

    def restore(self, transaction_id: int) -> None:
        self.connection.execute("UPDATE transactions SET deleted_at = NULL WHERE id = ?", (transaction_id,))

    def list_deleted(self):
        return self.connection.execute(
            """SELECT t.* FROM transactions t
               WHERE t.deleted_at IS NOT NULL
               ORDER BY t.deleted_at DESC, t.id DESC"""
        ).fetchall()
