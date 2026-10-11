from __future__ import annotations

import calendar
import sqlite3
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from repositories import AccountRepository, RecurrenceRepository, TransactionRepository
from .validation import month_date


class FinanceService:
    """Regras financeiras independentes da camada HTTP."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.accounts = AccountRepository(connection)
        self.recurrences = RecurrenceRepository(connection)
        self.transactions = TransactionRepository(connection)

    def ensure_recurrences_for_month(self, year: int, month: int) -> None:
        first_day = date(year, month, 1).isoformat()
        last_day = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
        rules = self.recurrences.list_active_for_period(first_day, last_day)
        created_at = datetime.now().isoformat(timespec="seconds")
        entries = []
        for rule in rules:
            due = month_date(year, month, rule["day_of_month"]).isoformat()
            if due < rule["start_date"] or (rule["end_date"] and due > rule["end_date"]):
                continue
            entries.append((rule, due, created_at))
        self.transactions.create_recurrences(entries)
        self.connection.commit()

    @staticmethod
    def _consolidate_credit_card_invoices(rows, today: date):
        consolidated = []
        invoices = {}
        for source_row in rows:
            row = dict(source_row)
            if not row.get("credit_card_id"):
                row["is_credit_card_invoice"] = False
                consolidated.append(row)
                continue

            billing_month = row["due_date"][:7]
            key = (row["credit_card_id"], billing_month)
            invoice = invoices.get(key)
            if invoice is None:
                invoice = {
                    "id": None,
                    "kind": "expense",
                    "description": f"Fatura {row.get('credit_card_name') or 'do cartão'}",
                    "category": "Cartão de crédito",
                    "amount": 0,
                    "due_date": row["due_date"],
                    "status": "settled",
                    "purchase_date": None,
                    "import_source": None,
                    "installment_group": None,
                    "installment_number": None,
                    "installment_total": None,
                    "card_installment_projected": 0,
                    "credit_card_id": row["credit_card_id"],
                    "billing_month": billing_month,
                    "overdue": 0,
                    "is_credit_card_invoice": True,
                }
                invoices[key] = invoice
                consolidated.append(invoice)
            invoice["amount"] += row["amount"]
            if row["status"] == "pending":
                invoice["status"] = "pending"
                invoice["overdue"] = int(row["due_date"] < today.isoformat())
        return consolidated

    def dashboard(self, period: date, today: date | None = None) -> dict:
        today = today or date.today()
        start = period.replace(day=1).isoformat()
        end = period.replace(day=calendar.monthrange(period.year, period.month)[1]).isoformat()
        bank_balance = self.accounts.total_balance()
        current_month = today.replace(day=1)
        opening_balance = bank_balance
        if period > current_month:
            previous_month = period.month - 1 or 12
            previous_year = period.year - (period.month == 1)
            bridge_end = date(
                previous_year,
                previous_month,
                calendar.monthrange(previous_year, previous_month)[1],
            ).isoformat()
            bridge_totals = self.transactions.pending_totals(current_month.isoformat(), bridge_end)
            bridge_pending = {row["kind"]: row["total"] for row in bridge_totals}
            opening_balance += (
                bridge_pending.get("income", 0) - bridge_pending.get("expense", 0)
            )
        totals = self.transactions.pending_totals(start, end)
        pending = {row["kind"]: row["total"] for row in totals}
        pending_income = pending.get("income", 0)
        pending_expense = pending.get("expense", 0)
        return {
            "accounts": self.accounts.list_all(),
            "bank_balance": bank_balance,
            "opening_balance": opening_balance,
            "is_future_month": period > current_month,
            "pending_income": pending_income,
            "pending_expense": pending_expense,
            "forecast": opening_balance + pending_income - pending_expense,
            "items": self._consolidate_credit_card_invoices(
                self.transactions.list_for_period(start, end, today.isoformat()), today
            ),
            "today": today.isoformat(),
        }

    def update_account_balance(self, account_id: int, balance: float) -> bool:
        updated = self.accounts.update_balance(
            account_id, balance, datetime.now().isoformat(timespec="seconds")
        )
        self.connection.commit()
        return updated

    def list_accounts(self):
        return self.accounts.list_all()

    def create_entry(self, *, kind: str, description: str, category: str, amount: float,
                     due_date: date, fixed_monthly: bool = False) -> dict:
        if fixed_monthly:
            recurrence_id = self.recurrences.create(
                kind=kind, description=description, category=category, amount=amount,
                day_of_month=due_date.day, start_date=due_date.isoformat(), account_id=None,
            )
            self.connection.commit()
            self.ensure_recurrences_for_month(due_date.year, due_date.month)
            return {"created": "recurrence", "recurrence_id": recurrence_id}
        transaction_id = self.transactions.create(
            kind=kind, description=description, category=category, amount=amount,
            due_date=due_date.isoformat(), account_id=None,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        self.connection.commit()
        return {"created": "transaction", "transaction_id": transaction_id}

    def transaction_page(self, kind: str, today: date) -> dict:
        return {
            "rows": self._consolidate_credit_card_invoices(
                self.transactions.list_for_kind(kind, today.isoformat()), today
            ),
            "rules": self.recurrences.list_by_kind(kind),
        }

    def create_installments(self, *, kind: str, description: str, category: str,
                            total_amount: float, first_due_date: date, count: int) -> dict:
        amount = Decimal(str(total_amount))
        base = (amount / count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        group = f"{kind}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        created_at = datetime.now().isoformat(timespec="seconds")
        entries = []
        for number in range(1, count + 1):
            year = first_due_date.year + (first_due_date.month - 1 + number - 1) // 12
            month = (first_due_date.month - 1 + number - 1) % 12 + 1
            part = base if number < count else amount - base * (count - 1)
            entries.append(dict(
                kind=kind, description=description, category=category, amount=float(part),
                due_date=month_date(year, month, first_due_date.day).isoformat(),
                created_at=created_at, account_id=None, installment_group=group,
                installment_number=number, installment_total=count,
            ))
        transaction_ids = self.transactions.create_many(entries)
        self.connection.commit()
        return {"installment_group": group, "transaction_ids": transaction_ids, "count": count}

    def toggle_transaction_status(self, transaction_id: int, today: date) -> str | None:
        transaction = self.transactions.find_active(transaction_id)
        if not transaction:
            return None
        status = "pending" if transaction["status"] == "settled" else "settled"
        settled_date = today.isoformat() if status == "settled" else None
        self.transactions.update_status(transaction_id, status, settled_date)
        self.connection.commit()
        return status

    def toggle_credit_card_invoice_status(
        self, card_id: int, billing_month: date, today: date
    ) -> str | None:
        start = billing_month.replace(day=1).isoformat()
        end = billing_month.replace(
            day=calendar.monthrange(billing_month.year, billing_month.month)[1]
        ).isoformat()
        counts = self.transactions.credit_card_invoice_status_counts(card_id, start, end)
        if not counts or counts["total"] == 0:
            return None
        status = "settled" if counts["pending"] else "pending"
        settled_date = today.isoformat() if status == "settled" else None
        self.transactions.update_credit_card_invoice_status(
            card_id, start, end, status, settled_date
        )
        self.connection.commit()
        return status

    def delete_transaction(self, transaction_id: int) -> bool:
        if not self.transactions.find_active(transaction_id):
            return False
        self.transactions.soft_delete(transaction_id, datetime.now().isoformat(timespec="seconds"))
        self.connection.commit()
        return True

    def restore_transaction(self, transaction_id: int) -> bool:
        if not self.transactions.find_deleted(transaction_id):
            return False
        self.transactions.restore(transaction_id)
        self.connection.commit()
        return True

    def list_deleted_transactions(self):
        return self.transactions.list_deleted()

    def toggle_recurrence(self, recurrence_id: int) -> bool:
        updated = self.recurrences.toggle(recurrence_id)
        self.connection.commit()
        return updated

    def projection(self, today: date, months_count: int = 12) -> dict:
        starting_balance = self.accounts.total_balance()
        rules = self.recurrences.list_active()
        balance = starting_balance
        months = []
        for offset in range(months_count):
            year = today.year + (today.month - 1 + offset) // 12
            month = (today.month - 1 + offset) % 12 + 1
            first = date(year, month, 1)
            last = date(year, month, calendar.monthrange(year, month)[1])
            totals = {"income": 0, "expense": 0}
            for rule in rules:
                if (rule["start_date"] <= last.isoformat()
                        and (not rule["end_date"] or rule["end_date"] >= first.isoformat())):
                    totals[rule["kind"]] += rule["amount"]
            opening = balance
            balance += totals["income"] - totals["expense"]
            months.append({
                "label": f"{calendar.month_name[month].capitalize()} {year}",
                "period": f"{year:04d}-{month:02d}",
                "opening": opening,
                "income": totals["income"],
                "expense": totals["expense"],
                "closing": balance,
            })
        return {"starting_balance": starting_balance, "months": months}
