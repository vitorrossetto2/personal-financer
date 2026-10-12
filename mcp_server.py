from __future__ import annotations

import calendar
import os
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

import app as finance_app


if database_override := os.environ.get("NOVOSALDO_DATABASE"):
    finance_app.DATABASE = Path(database_override).expanduser().resolve()


INSTRUCTIONS = (
    "Use este servidor para consultar e operar o NovoSaldo, sempre no mesmo banco SQLite da aplicação web. "
    "Antes de alterar dados, consulte contas, lançamentos ou recorrências quando precisar confirmar IDs. "
    "Datas usam YYYY-MM-DD e valores monetários são números positivos. Ferramentas cadastrar_*, atualizar_*, "
    "definir_* e importar_* alteram dados; as demais são somente leitura. A importação de fatura é idempotente. "
    "Dar baixa em um lançamento não altera automaticamente o saldo bancário."
)

server = MCPServer(
    name="novosaldo",
    title="NovoSaldo",
    description="Ferramentas para operar o controle financeiro pessoal NovoSaldo.",
    instructions=INSTRUCTIONS,
    version="1.0.0",
)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


@contextmanager
def database():
    with finance_app.app.app_context():
        finance_app.initialize_database()
        today = date.today()
        finance_app.ensure_recurrences_for_month(today.year, today.month)
        yield finance_app.db()


def iso_date(value: str, field: str = "data") -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as error:
        raise ValueError(f"{field} deve usar o formato YYYY-MM-DD.") from error


def positive_money(value: float) -> float:
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise ValueError("O valor deve ser maior que zero.")
    return float(amount)


def valid_kind(kind: str) -> str:
    if kind not in ("income", "expense"):
        raise ValueError("tipo deve ser 'income' ou 'expense'.")
    return kind


def valid_status(status: str) -> str:
    if status not in ("pending", "settled"):
        raise ValueError("status deve ser 'pending' ou 'settled'.")
    return status


@server.tool(annotations=READ_ONLY, structured_output=True)
def listar_contas() -> dict[str, Any]:
    """Lista contas bancárias, saldos e data da última atualização."""
    with database() as connection:
        rows = connection.execute("SELECT id, name, bank, balance, updated_at FROM accounts ORDER BY id").fetchall()
        return {"contas": [dict(row) for row in rows]}


@server.tool(annotations=IDEMPOTENT_WRITE, structured_output=True)
def atualizar_saldo_conta(account_id: int, balance: float) -> dict[str, Any]:
    """Define o saldo atual de uma conta existente. Não cria movimentações."""
    amount = positive_money(balance)
    with database() as connection:
        account = connection.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not account:
            raise ValueError("Conta não encontrada.")
        updated_at = datetime.now().isoformat(timespec="seconds")
        connection.execute(
            "UPDATE accounts SET balance = ?, updated_at = ? WHERE id = ?",
            (amount, updated_at, account_id),
        )
        connection.commit()
        return {"id": account_id, "name": account["name"], "balance": amount, "updated_at": updated_at}


@server.tool(annotations=READ_ONLY, structured_output=True)
def resumo_mensal(year: int, month: int) -> dict[str, Any]:
    """Retorna saldos, receitas, despesas, previsão e contagens de um mês."""
    if month < 1 or month > 12 or year < 1900 or year > 9999:
        raise ValueError("Ano ou mês inválido.")
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    with database() as connection:
        finance_app.ensure_recurrences_for_month(year, month)
        bank_balance = connection.execute("SELECT COALESCE(SUM(balance), 0) FROM accounts").fetchone()[0]
        rows = connection.execute(
            """SELECT kind, status, COUNT(*) count, COALESCE(SUM(amount), 0) total
               FROM transactions
               WHERE due_date BETWEEN ? AND ? AND deleted_at IS NULL
               GROUP BY kind, status""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        totals = {f"{row['kind']}_{row['status']}": row["total"] for row in rows}
        pending_income = totals.get("income_pending", 0)
        pending_expense = totals.get("expense_pending", 0)
        return {
            "period": f"{year:04d}-{month:02d}",
            "bank_balance": bank_balance,
            "pending_income": pending_income,
            "pending_expense": pending_expense,
            "forecast": bank_balance + pending_income - pending_expense,
            "breakdown": [dict(row) for row in rows],
        }


@server.tool(annotations=READ_ONLY, structured_output=True)
def listar_lancamentos(
    kind: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Lista lançamentos com filtros opcionais por tipo, status e vencimento."""
    if kind is not None:
        valid_kind(kind)
    if status is not None:
        valid_status(status)
    if limit < 1 or limit > 500:
        raise ValueError("limit deve ficar entre 1 e 500.")
    clauses = ["deleted_at IS NULL"]
    parameters: list[object] = []
    if kind:
        clauses.append("kind = ?")
        parameters.append(kind)
    if status:
        clauses.append("status = ?")
        parameters.append(status)
    if date_from:
        clauses.append("due_date >= ?")
        parameters.append(iso_date(date_from, "date_from").isoformat())
    if date_to:
        clauses.append("due_date <= ?")
        parameters.append(iso_date(date_to, "date_to").isoformat())
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    parameters.append(limit)
    with database() as connection:
        rows = connection.execute(
            f"""SELECT id, kind, description, category, amount, due_date, status, settled_date,
                       installment_group, installment_number, installment_total, purchase_date, import_source
                FROM transactions{where} ORDER BY due_date DESC, id DESC LIMIT ?""",
            parameters,
        ).fetchall()
        return {"lancamentos": [dict(row) for row in rows], "count": len(rows)}


@server.tool(annotations=WRITE, structured_output=True)
def cadastrar_lancamento(
    kind: str,
    description: str,
    amount: float,
    due_date: str,
    category: str = "",
    fixed_monthly: bool = False,
) -> dict[str, Any]:
    """Cadastra uma receita/despesa única ou uma recorrência mensal."""
    valid_kind(kind)
    description = description.strip()
    if not description:
        raise ValueError("A descrição é obrigatória.")
    amount = positive_money(amount)
    due = iso_date(due_date, "due_date")
    now = datetime.now().isoformat(timespec="seconds")
    with database() as connection:
        if fixed_monthly:
            cursor = connection.execute(
                """INSERT INTO recurrences (kind, description, category, amount, day_of_month, start_date)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (kind, description, category.strip(), amount, due.day, due.isoformat()),
            )
            connection.commit()
            recurrence_id = cursor.lastrowid
            finance_app.ensure_recurrences_for_month(due.year, due.month)
            transaction = connection.execute(
                "SELECT id FROM transactions WHERE recurrence_id = ? AND due_date = ?",
                (recurrence_id, due.isoformat()),
            ).fetchone()
            return {
                "created": "recurrence",
                "recurrence_id": recurrence_id,
                "transaction_id": transaction["id"] if transaction else None,
            }
        cursor = connection.execute(
            """INSERT INTO transactions (kind, description, category, amount, due_date, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (kind, description, category.strip(), amount, due.isoformat(), now),
        )
        connection.commit()
        return {"created": "transaction", "transaction_id": cursor.lastrowid}


@server.tool(annotations=WRITE, structured_output=True)
def cadastrar_parcelamento(
    kind: str,
    description: str,
    total_amount: float,
    first_due_date: str,
    installment_total: int,
    category: str = "",
) -> dict[str, Any]:
    """Divide um valor total em parcelas mensais e cadastra todos os lançamentos."""
    valid_kind(kind)
    description = description.strip()
    if not description:
        raise ValueError("A descrição é obrigatória.")
    if installment_total < 2 or installment_total > 240:
        raise ValueError("installment_total deve ficar entre 2 e 240.")
    due = iso_date(first_due_date, "first_due_date")
    amount = Decimal(str(positive_money(total_amount)))
    base = (amount / installment_total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    group = f"{kind}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    now = datetime.now().isoformat(timespec="seconds")
    transaction_ids = []
    with database() as connection:
        for number in range(1, installment_total + 1):
            year = due.year + (due.month - 1 + number - 1) // 12
            month = (due.month - 1 + number - 1) % 12 + 1
            part = base if number < installment_total else amount - base * (installment_total - 1)
            installment_due = finance_app.month_date(year, month, due.day).isoformat()
            cursor = connection.execute(
                """INSERT INTO transactions
                   (kind, description, category, amount, due_date, installment_group,
                    installment_number, installment_total, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (kind, description, category.strip(), float(part), installment_due, group,
                 number, installment_total, now),
            )
            transaction_ids.append(cursor.lastrowid)
        connection.commit()
    return {"installment_group": group, "transaction_ids": transaction_ids, "count": len(transaction_ids)}


@server.tool(annotations=IDEMPOTENT_WRITE, structured_output=True)
def definir_status_lancamento(transaction_id: int, status: str) -> dict[str, Any]:
    """Define um lançamento como pending ou settled de forma idempotente."""
    valid_status(status)
    with database() as connection:
        transaction = connection.execute(
            "SELECT id, status FROM transactions WHERE id = ? AND deleted_at IS NULL", (transaction_id,)
        ).fetchone()
        if not transaction:
            raise ValueError("Lançamento não encontrado.")
        settled_date = date.today().isoformat() if status == "settled" else None
        connection.execute(
            "UPDATE transactions SET status = ?, settled_date = ? WHERE id = ?",
            (status, settled_date, transaction_id),
        )
        connection.commit()
        return {"transaction_id": transaction_id, "status": status, "settled_date": settled_date}


@server.tool(annotations=READ_ONLY, structured_output=True)
def listar_recorrencias(kind: str | None = None, active: bool | None = None) -> dict[str, Any]:
    """Lista receitas e despesas fixas, com filtros opcionais."""
    if kind is not None:
        valid_kind(kind)
    clauses = []
    parameters: list[object] = []
    if kind:
        clauses.append("kind = ?")
        parameters.append(kind)
    if active is not None:
        clauses.append("active = ?")
        parameters.append(1 if active else 0)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with database() as connection:
        rows = connection.execute(
            f"""SELECT id, kind, description, category, amount, day_of_month, start_date, end_date, active
                FROM recurrences{where} ORDER BY kind, description""",
            parameters,
        ).fetchall()
        return {"recorrencias": [dict(row) for row in rows], "count": len(rows)}


@server.tool(annotations=IDEMPOTENT_WRITE, structured_output=True)
def definir_recorrencia_ativa(recurrence_id: int, active: bool) -> dict[str, Any]:
    """Ativa ou pausa uma recorrência de forma idempotente."""
    with database() as connection:
        rule = connection.execute("SELECT id FROM recurrences WHERE id = ?", (recurrence_id,)).fetchone()
        if not rule:
            raise ValueError("Recorrência não encontrada.")
        connection.execute("UPDATE recurrences SET active = ? WHERE id = ?", (1 if active else 0, recurrence_id))
        connection.commit()
        return {"recurrence_id": recurrence_id, "active": active}


@server.tool(annotations=READ_ONLY, structured_output=True)
def projecao_12_meses() -> dict[str, Any]:
    """Calcula a projeção de 12 meses usando apenas recorrências ativas."""
    with database() as connection:
        starting_balance = connection.execute("SELECT COALESCE(SUM(balance), 0) FROM accounts").fetchone()[0]
        rules = connection.execute("SELECT * FROM recurrences WHERE active = 1").fetchall()
        today = date.today()
        balance = starting_balance
        months = []
        for offset in range(12):
            year = today.year + (today.month - 1 + offset) // 12
            month = (today.month - 1 + offset) % 12 + 1
            first = date(year, month, 1)
            last = date(year, month, calendar.monthrange(year, month)[1])
            income = sum(
                rule["amount"] for rule in rules
                if rule["kind"] == "income" and rule["start_date"] <= last.isoformat()
                and (not rule["end_date"] or rule["end_date"] >= first.isoformat())
            )
            expense = sum(
                rule["amount"] for rule in rules
                if rule["kind"] == "expense" and rule["start_date"] <= last.isoformat()
                and (not rule["end_date"] or rule["end_date"] >= first.isoformat())
            )
            opening = balance
            balance += income - expense
            months.append({
                "period": f"{year:04d}-{month:02d}", "opening": opening,
                "income": income, "expense": expense, "closing": balance,
            })
        return {"starting_balance": starting_balance, "months": months}


def statement_path(path: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file() or resolved.suffix.lower() != ".xlsx":
        raise ValueError("Informe o caminho de um arquivo .xlsx existente.")
    if resolved.stat().st_size > finance_app.app.config["MAX_CONTENT_LENGTH"]:
        raise ValueError("A planilha excede o limite de 8 MB.")
    return resolved


@server.tool(annotations=READ_ONLY, structured_output=True)
def listar_cartoes() -> dict[str, Any]:
    """Lista os cartões de crédito cadastrados e seus dias de pagamento."""
    with database() as connection:
        cards = finance_app.CreditCardService(connection).list_cards()
        return {"cartoes": [dict(card) for card in cards], "count": len(cards)}


@server.tool(annotations=WRITE, structured_output=True)
def cadastrar_cartao(name: str, issuer: str, last_four: str, payment_day: int) -> dict[str, Any]:
    """Cadastra um cartão. Emissores aceitos: itau e bradesco."""
    with database() as connection:
        card_id = finance_app.CreditCardService(connection).create_card(
            name=name, issuer=issuer, last_four=last_four, payment_day=payment_day
        )
        return {"card_id": card_id}


@server.tool(annotations=READ_ONLY, structured_output=True)
def analisar_fatura_cartao(path: str, issuer: str = "itau") -> dict[str, Any]:
    """Analisa uma fatura .xlsx sem gravar dados e retorna totais e amostra."""
    resolved = statement_path(path)
    statement = finance_app.parse_credit_card_statement(resolved, issuer)
    return {
        "path": str(resolved),
        "due_date": statement["due_date"],
        "card_label": statement["card_label"],
        "entries": len(statement["entries"]),
        "skipped": statement["skipped"],
        "total": round(sum(entry["amount"] for entry in statement["entries"]), 2),
        "sample": statement["entries"][:5],
    }


@server.tool(annotations=IDEMPOTENT_WRITE, structured_output=True)
def importar_fatura_cartao(
    path: str, credit_card_id: int, category: str = "Cartão de crédito"
) -> dict[str, Any]:
    """Importa as compras de uma fatura .xlsx; reenvios não duplicam despesas."""
    resolved = statement_path(path)
    with database() as connection:
        service = finance_app.CreditCardService(connection)
        card = service.cards.find_active(credit_card_id)
        if not card:
            raise ValueError("Cartão não encontrado.")
        statement = finance_app.parse_credit_card_statement(resolved, card["issuer"])
        result = service.save_statement(
            statement, resolved.name, credit_card_id=credit_card_id, category=category
        )
    return {"path": str(resolved), **result}


if __name__ == "__main__":
    import uvicorn
    from mcp_auth import MCPTokenAuth

    host = os.environ.get("NOVOSALDO_MCP_HOST", "127.0.0.1")
    application = server.streamable_http_app(streamable_http_path="/mcp", host=host)
    application.add_middleware(MCPTokenAuth, token=os.environ.get("NOVOSALDO_MCP_TOKEN", ""))
    uvicorn.run(application, host=host, port=int(os.environ.get("NOVOSALDO_MCP_PORT", "8000")))
