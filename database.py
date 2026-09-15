from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    bank TEXT NOT NULL,
    balance REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('income','expense')),
    description TEXT NOT NULL,
    category TEXT,
    amount REAL NOT NULL CHECK(amount > 0),
    day_of_month INTEGER NOT NULL CHECK(day_of_month BETWEEN 1 AND 31),
    start_date TEXT NOT NULL,
    end_date TEXT,
    account_id INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(account_id) REFERENCES accounts(id)
);
CREATE TABLE IF NOT EXISTS credit_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    issuer TEXT NOT NULL CHECK(issuer IN ('itau','bradesco')),
    last_four TEXT NOT NULL CHECK(length(last_four) = 4),
    payment_day INTEGER NOT NULL CHECK(payment_day BETWEEN 1 AND 31),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('income','expense')),
    description TEXT NOT NULL,
    category TEXT,
    amount REAL NOT NULL CHECK(amount > 0),
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','settled')),
    settled_date TEXT,
    account_id INTEGER,
    recurrence_id INTEGER,
    installment_group TEXT,
    installment_number INTEGER,
    installment_total INTEGER,
    purchase_date TEXT,
    import_source TEXT,
    import_key TEXT UNIQUE,
    credit_card_id INTEGER,
    cardholder_name TEXT,
    card_last_four TEXT,
    card_installment_projected INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    deleted_at TEXT,
    FOREIGN KEY(account_id) REFERENCES accounts(id),
    FOREIGN KEY(recurrence_id) REFERENCES recurrences(id),
    FOREIGN KEY(credit_card_id) REFERENCES credit_cards(id),
    UNIQUE(recurrence_id, due_date)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(transactions)")}
    for name, definition in {
        "purchase_date": "TEXT",
        "import_source": "TEXT",
        "import_key": "TEXT",
        "deleted_at": "TEXT",
        "credit_card_id": "INTEGER REFERENCES credit_cards(id)",
        "cardholder_name": "TEXT",
        "card_last_four": "TEXT",
        "card_installment_projected": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if name not in existing_columns:
            connection.execute(f"ALTER TABLE transactions ADD COLUMN {name} {definition}")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_import_key "
        "ON transactions(import_key) WHERE import_key IS NOT NULL"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_transactions_credit_card_due_date "
        "ON transactions(credit_card_id, due_date)"
    )
    now = datetime.now().isoformat(timespec="seconds")
    for name in ("Itaú", "Bradesco"):
        connection.execute(
            "INSERT OR IGNORE INTO accounts (name, bank, balance, updated_at) VALUES (?, ?, 0, ?)",
            (name, name, now),
        )
    # Lançamentos pessoais não pertencem a uma conta bancária específica.
    connection.execute("UPDATE transactions SET account_id = NULL")
    connection.execute("UPDATE recurrences SET account_id = NULL")
    connection.commit()
