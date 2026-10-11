"""Small DB-API facade over D1's binding, plus atomic write batches.

D1 executes standalone statements immediately. Multi-write service operations
must use execute_batch() or the card-import planner below, never BEGIN/COMMIT.
"""
from contextlib import contextmanager
import sqlite3

from database import SCHEMA


class Row(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class Cursor:
    def __init__(self, result):
        self.rows = [Row(row) for row in result.get("results", [])]
        self.position = 0
        meta = result.get("meta", {})
        self.rowcount = meta.get("changes", 0)
        self.lastrowid = meta.get("last_row_id")

    def fetchone(self):
        if self.position >= len(self.rows):
            return None
        row = self.rows[self.position]
        self.position += 1
        return row

    def fetchall(self):
        rows = self.rows[self.position:]
        self.position = len(self.rows)
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class D1Connection:
    def __init__(self, binding, *, run_sync=None):
        if run_sync is None:
            from pyodide.ffi import run_sync
        self.binding = binding
        self.run_sync = run_sync

    def _prepare(self, sql, parameters):
        statement = self.binding.prepare(sql)
        return statement.bind(*parameters) if parameters else statement

    @staticmethod
    def _python(value):
        return value.to_py() if hasattr(value, "to_py") else value

    def _call(self, operation):
        try:
            return self._python(self.run_sync(operation))
        except Exception as error:
            if any(marker in str(error).lower() for marker in (
                "constraint failed", "constraint violation", "sqlite_constraint",
            )):
                raise sqlite3.IntegrityError(str(error)) from error
            raise

    def execute(self, sql, parameters=()):
        return Cursor(self._call(self._prepare(sql, parameters).all()))

    def execute_batch(self, statements):
        prepared = [self._prepare(sql, params) for sql, params in statements]
        if not prepared:
            return []
        results = self._call(self.binding.batch(prepared))
        return [Cursor(self._python(result)) for result in results]

    def commit(self):
        # Standalone writes already committed by D1; batch() is atomic.
        pass

    def close(self):
        pass

    @contextmanager
    def plan_card_import(self, card_id):
        """Evaluate existing reconciliation rules locally, then batch only writes.

        The temporary SQLite database is an operation planner, never storage.
        IDs allocated here are not sent to D1. Updates address import_key so a
        newly planned row can also be confirmed within this same statement.
        Unique keys cause conflicting concurrent inserts to roll back the entire
        batch; the caller may safely reimport after that conflict.
        """
        local = sqlite3.connect(":memory:")
        local.row_factory = sqlite3.Row
        try:
            local.executescript(SCHEMA)
            for table, sql in (
                ("credit_cards", "SELECT * FROM credit_cards WHERE id = ?"),
                ("transactions", "SELECT * FROM transactions WHERE credit_card_id = ?"),
            ):
                for row in self.execute(sql, (card_id,)).fetchall():
                    columns = list(row.keys())
                    local.execute(
                        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                        tuple(row[column] for column in columns),
                    )
            local.commit()
            yield CardImportPlan(self, local)
        finally:
            local.close()


class CardImportPlan:
    def __init__(self, remote, local):
        self.remote = remote
        self.local = local
        self.statements = []

    def execute(self, sql, parameters=()):
        remote_sql, remote_parameters = sql, tuple(parameters)
        verb = sql.lstrip().split(None, 1)[0].upper()
        if verb == "UPDATE" and "WHERE id = ?" in sql:
            row = self.local.execute(
                "SELECT import_key FROM transactions WHERE id = ?", (parameters[-1],)
            ).fetchone()
            if row is not None and row["import_key"] is not None:
                remote_sql = sql.replace("WHERE id = ?", "WHERE import_key = ?")
                remote_parameters = (*parameters[:-1], row["import_key"])
        cursor = self.local.execute(sql, parameters)
        if verb in {"INSERT", "UPDATE", "DELETE"}:
            self.statements.append((remote_sql, remote_parameters))
        return cursor

    def commit(self):
        self.remote.execute_batch(self.statements)
        self.statements.clear()
        self.local.commit()
