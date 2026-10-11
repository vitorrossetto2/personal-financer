
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_import_key
ON transactions(import_key) WHERE import_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_transactions_credit_card_due_date
ON transactions(credit_card_id, due_date);
INSERT OR IGNORE INTO accounts (name, bank, balance, updated_at)
VALUES ('Itaú', 'Itaú', 0, strftime('%Y-%m-%dT%H:%M:%S', 'now'));
INSERT OR IGNORE INTO accounts (name, bank, balance, updated_at)
VALUES ('Bradesco', 'Bradesco', 0, strftime('%Y-%m-%dT%H:%M:%S', 'now'));
