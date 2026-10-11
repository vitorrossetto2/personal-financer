from __future__ import annotations

import ipaddress
import os
import secrets
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from zipfile import BadZipFile

from dotenv import load_dotenv
from flask import Flask, abort, flash, g, has_request_context, jsonify, redirect, render_template, request, session, url_for
from openpyxl.utils.exceptions import InvalidFileException
from werkzeug.utils import secure_filename

from database import connect, initialize
from services import (
    CreditCardService,
    FinanceService,
    kind_label,
    month_date,
    parse_amount,
    parse_credit_card_statement,
)


BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "instance" / "financeiro.db"

IS_WORKER = sys.platform == "emscripten"
if not IS_WORKER:
    load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=None if IS_WORKER else (os.environ.get("NOVOSALDO_SECRET_KEY") or secrets.token_hex(32)),
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("NOVOSALDO_HTTPS", "").lower() in {"1", "true", "yes"},
    PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
    GOOGLE_CLIENT_ID=os.environ.get("GOOGLE_CLIENT_ID", "").strip(),
    GOOGLE_CLIENT_SECRET=os.environ.get("GOOGLE_CLIENT_SECRET", "").strip(),
    ALLOWED_GOOGLE_EMAIL=os.environ.get("NOVOSALDO_ALLOWED_EMAIL", "").strip().casefold(),
    LOCAL_AUTH_DISABLED=os.environ.get("NOVOSALDO_LOCAL_AUTH_DISABLED", "").lower() in {"1", "true", "yes"},
    AUTH_DISABLED_FOR_TESTS=False,
)

if IS_WORKER:
    from worker_auth import WorkerGoogleOAuth
    google = WorkerGoogleOAuth()
else:
    from authlib.integrations.flask_client import OAuth
    oauth = OAuth(app)
    google = oauth.register(
        name="google",
        client_id=app.config["GOOGLE_CLIENT_ID"],
        client_secret=app.config["GOOGLE_CLIENT_SECRET"],
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )



def db():
    if "db" not in g:
        if has_request_context() and "workers.env" in request.environ:
            from d1_database import D1Connection
            g.db = D1Connection(request.environ["workers.env"].DB)
        else:
            g.db = connect(DATABASE)
    return g.db


def finance_service() -> FinanceService:
    return FinanceService(db())


def credit_card_service() -> CreditCardService:
    return CreditCardService(db())


def initialize_database() -> None:
    if not has_request_context() or "workers.env" not in request.environ:
        initialize(db())


def ensure_recurrences_for_month(year: int, month: int) -> None:
    finance_service().ensure_recurrences_for_month(year, month)


def save_credit_card_statement(statement: dict, filename: str,
                               *, credit_card_id: int,
                               category: str = "Cartão de crédito") -> dict:
    return credit_card_service().save_statement(
        statement, filename, credit_card_id=credit_card_id, category=category
    )


def accounts():
    return finance_service().list_accounts()


def auth_configuration_errors() -> list[str]:
    missing = []
    if not app.config["GOOGLE_CLIENT_ID"]:
        missing.append("GOOGLE_CLIENT_ID")
    if not app.config["GOOGLE_CLIENT_SECRET"]:
        missing.append("GOOGLE_CLIENT_SECRET")
    if not app.config["ALLOWED_GOOGLE_EMAIL"]:
        missing.append("NOVOSALDO_ALLOWED_EMAIL")
    if not (app.config.get("WORKER_SECRET_CONFIGURED") or os.environ.get("NOVOSALDO_SECRET_KEY")) and not app.testing:
        missing.append("NOVOSALDO_SECRET_KEY")
    return missing


@app.before_request
def require_authenticated_user():
    try:
        is_local_request = ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        is_local_request = False
    if (
        app.config["AUTH_DISABLED_FOR_TESTS"]
        or (app.config["LOCAL_AUTH_DISABLED"] and is_local_request)
        or request.endpoint in {"login", "google_login", "google_callback", "static"}
    ):
        return None
    if "user" not in session:
        if request.method == "GET" and request.path.startswith("/") and not request.path.startswith("//"):
            session["login_next"] = request.full_path.rstrip("?")
        return redirect(url_for("login"))
    return None


@app.before_request
def prepare_request():
    initialize_database()
    today = date.today()
    ensure_recurrences_for_month(today.year, today.month)


@app.teardown_appcontext
def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


@app.get("/login")
def login():
    if "user" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html", configuration_errors=auth_configuration_errors())


@app.get("/auth/google")
def google_login():
    if auth_configuration_errors():
        flash("Configure o login com Google antes de continuar.", "error")
        return redirect(url_for("login"))
    return google.authorize_redirect(url_for("google_callback", _external=True))


@app.get("/auth/google/callback")
def google_callback():
    try:
        token = google.authorize_access_token()
        userinfo = token.get("userinfo") or google.get("userinfo", token=token).json()
    except Exception:
        app.logger.exception("Falha ao concluir o login com Google")
        session.clear()
        flash("Não foi possível validar o login com Google. Tente novamente.", "error")
        return redirect(url_for("login"))

    email = str(userinfo.get("email", "")).strip().casefold()
    verified = userinfo.get("email_verified") in {True, "true", "True", 1, "1"}
    if not verified or not secrets.compare_digest(email, app.config["ALLOWED_GOOGLE_EMAIL"]):
        session.clear()
        flash("Esta conta Google não tem permissão para acessar o NovoSaldo.", "error")
        return redirect(url_for("login"))

    next_url = session.pop("login_next", None)
    session.clear()
    session.permanent = True
    session["user"] = {
        "sub": userinfo.get("sub"), "email": email,
        "name": userinfo.get("name") or email, "picture": userinfo.get("picture"),
    }
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(url_for("dashboard"))


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.template_filter("brl")
def brl(value):
    return f"R$ {float(value or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


@app.template_filter("date_br")
def date_br(value):
    if not value:
        return "—"
    return datetime.strptime(value, "%Y-%m-%d").strftime("%d/%m/%Y")


@app.get("/")
def dashboard():
    today = date.today()
    selected_month = request.args.get("month", today.strftime("%Y-%m"))
    try:
        period = datetime.strptime(selected_month, "%Y-%m").date()
    except ValueError:
        flash("Selecione um mês válido.", "error")
        return redirect(url_for("dashboard"))

    recurrence_month = today.replace(day=1)
    while recurrence_month <= period:
        ensure_recurrences_for_month(recurrence_month.year, recurrence_month.month)
        recurrence_month = date(
            recurrence_month.year + (recurrence_month.month == 12),
            1 if recurrence_month.month == 12 else recurrence_month.month + 1,
            1,
        )
    previous = date(period.year - (period.month == 1), 12 if period.month == 1 else period.month - 1, 1)
    following = date(period.year + (period.month == 12), 1 if period.month == 12 else period.month + 1, 1)
    return render_template(
        "dashboard.html",
        selected_month=selected_month,
        previous_month=previous.strftime("%Y-%m"),
        next_month=following.strftime("%Y-%m"),
        **finance_service().dashboard(period, today),
    )


@app.route("/contas", methods=["GET", "POST"])
def manage_accounts():
    service = finance_service()
    if request.method == "POST":
        try:
            account_id = int(request.form["account_id"])
            amount = parse_amount(request.form["balance"], require_positive=False)
            service.update_account_balance(account_id, amount)
            flash("Saldo atualizado.", "success")
        except (ValueError, KeyError):
            flash("Não foi possível atualizar o saldo. Confira o valor informado.", "error")
        return redirect(url_for("manage_accounts"))
    return render_template("accounts.html", accounts=service.list_accounts())


@app.route("/lancamentos/<kind>", methods=["GET", "POST"])
def transactions(kind):
    if kind not in ("income", "expense"):
        abort(404)
    service = finance_service()
    if request.method == "POST":
        try:
            description = request.form["description"].strip()
            due = datetime.strptime(request.form["due_date"], "%Y-%m-%d").date()
            if not description:
                raise ValueError
            amount = parse_amount(request.form["amount"])
            fixed = bool(request.form.get("is_fixed"))
            service.create_entry(kind=kind, description=description,
                                 category=request.form.get("category", "").strip(),
                                 amount=amount, due_date=due, fixed_monthly=fixed)
            suffix = " fixa" if fixed else ""
            flash(f"{kind_label(kind).capitalize()}{suffix} cadastrada.", "success")
        except (ValueError, KeyError):
            flash("Preencha descrição, data e valor corretamente.", "error")
        return redirect(url_for("transactions", kind=kind))
    page = service.transaction_page(kind, date.today())
    return render_template("transactions.html", kind=kind, today=date.today().isoformat(), **page)


@app.route("/cartoes", methods=["GET", "POST"])
def credit_cards():
    service = credit_card_service()
    if request.method == "POST":
        try:
            card_id = service.create_card(
                name=request.form["name"], issuer=request.form["issuer"],
                last_four=request.form["last_four"], payment_day=int(request.form["payment_day"]),
            )
            flash("Cartão cadastrado.", "success")
            return redirect(url_for("credit_cards", card_id=card_id, month=date.today().strftime("%Y-%m")))
        except (ValueError, KeyError, sqlite3.IntegrityError):
            flash("Não foi possível cadastrar o cartão. Confira os dados e use um nome único.", "error")
            return redirect(url_for("credit_cards"))

    billing_month = request.args.get("month", date.today().strftime("%Y-%m"))
    try:
        card_id = int(request.args["card_id"]) if request.args.get("card_id") else None
        page = service.control_page(card_id, billing_month, date.today())
        current = datetime.strptime(billing_month, "%Y-%m").date()
    except (ValueError, TypeError):
        flash("Selecione um cartão e um mês válidos.", "error")
        return redirect(url_for("credit_cards"))
    previous = date(current.year - (current.month == 1), 12 if current.month == 1 else current.month - 1, 1)
    following = date(current.year + (current.month == 12), 1 if current.month == 12 else current.month + 1, 1)
    return render_template(
        "credit_cards.html", today=date.today().isoformat(),
        previous_month=previous.strftime("%Y-%m"), next_month=following.strftime("%Y-%m"), **page,
    )


@app.post("/cartoes/lancamentos")
def create_credit_card_expense():
    card_id = request.form.get("card_id")
    billing_month = request.form.get("billing_month")
    try:
        credit_card_service().create_expense(
            card_id=int(card_id), billing_month=billing_month,
            description=request.form["description"], category=request.form.get("category", ""),
            amount=parse_amount(request.form["amount"]),
            purchase_date=datetime.strptime(request.form["purchase_date"], "%Y-%m-%d").date(),
        )
        flash("Despesa de cartão cadastrada.", "success")
    except (ValueError, KeyError, TypeError):
        flash("Não foi possível cadastrar a despesa. Confira os dados informados.", "error")
    return redirect(url_for("credit_cards", card_id=card_id, month=billing_month))


@app.post("/cartoes/importar")
@app.post("/lancamentos/despesas/importar-cartao")
def import_credit_card_expenses():
    card_id = request.form.get("card_id")
    billing_month = request.form.get("billing_month")
    uploaded = request.files.get("statement")
    if not uploaded or not uploaded.filename:
        flash("Selecione uma planilha .xlsx para importar.", "error")
        return redirect(url_for("credit_cards", card_id=card_id, month=billing_month))
    filename = secure_filename(uploaded.filename)
    if Path(filename).suffix.lower() != ".xlsx":
        flash("Envie uma planilha no formato .xlsx.", "error")
        return redirect(url_for("credit_cards", card_id=card_id, month=billing_month))
    try:
        selected_card_id = int(card_id)
        card = credit_card_service().cards.find_active(selected_card_id)
        if not card:
            raise ValueError("Cartão não encontrado.")
        statement = parse_credit_card_statement(uploaded.stream, card["issuer"])
        category = request.form.get("category", "Cartão de crédito").strip() or "Cartão de crédito"
        result = credit_card_service().save_statement(
            statement, filename, credit_card_id=selected_card_id, category=category
        )
        billing_month = result["due_date"][:7]
        formatted_due_date = datetime.strptime(result["due_date"], "%Y-%m-%d").strftime("%d/%m/%Y")
        message = f"{result['imported']} nova(s) despesa(s) importada(s) para {formatted_due_date}."
        details = []
        if result["confirmed"]:
            details.append(f"{result['confirmed']} parcela(s) prevista(s) confirmada(s)")
        if result["scheduled"]:
            details.append(f"{result['scheduled']} parcela(s) futura(s) prevista(s)")
        if result["duplicates"]:
            details.append(f"{result['duplicates']} já existia(m) neste cartão e mês")
        if result["skipped"]:
            details.append(f"{result['skipped']} linha(s) de pagamento, crédito ou inválida(s) ignorada(s)")
        if details:
            message += " " + "; ".join(details) + "."
        flash(message, "success")
    except (BadZipFile, InvalidFileException, OSError, ValueError, KeyError) as error:
        flash(f"Não foi possível importar a fatura: {error}", "error")
    return redirect(url_for("credit_cards", card_id=card_id, month=billing_month))


@app.post("/parcelamentos/<kind>")
def installments(kind):
    if kind not in ("income", "expense"):
        abort(404)
    try:
        description = request.form["description"].strip()
        due = datetime.strptime(request.form["due_date"], "%Y-%m-%d").date()
        count = int(request.form["installment_total"])
        if not description or count < 2 or count > 240:
            raise ValueError
        finance_service().create_installments(
            kind=kind, description=description, category=request.form.get("category", "").strip(),
            total_amount=parse_amount(request.form["amount"]), first_due_date=due, count=count,
        )
        flash(f"Parcelamento de {count} parcelas criado.", "success")
    except (ValueError, KeyError):
        flash("Confira os dados do parcelamento. Use ao menos 2 parcelas.", "error")
    return redirect(url_for("transactions", kind=kind))


@app.post("/lancamentos/<int:transaction_id>/baixar")
def settle(transaction_id):
    status = finance_service().toggle_transaction_status(transaction_id, date.today())
    if status is None:
        abort(404)
    message = "Baixa desfeita." if status == "pending" else "Lançamento baixado."
    if request.accept_mimetypes.best == "application/json":
        return jsonify(status=status, message=message)
    flash(message, "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.post("/cartoes/<int:card_id>/faturas/<billing_month>/baixar")
def settle_credit_card_invoice(card_id, billing_month):
    try:
        period = datetime.strptime(billing_month, "%Y-%m").date()
    except ValueError:
        abort(404)
    status = finance_service().toggle_credit_card_invoice_status(card_id, period, date.today())
    if status is None:
        abort(404)
    message = "Baixa da fatura desfeita." if status == "pending" else "Fatura baixada."
    if request.accept_mimetypes.best == "application/json":
        return jsonify(status=status, message=message)
    flash(message, "success")
    return redirect(request.referrer or url_for("transactions", kind="expense"))


@app.post("/lancamentos/<int:transaction_id>/excluir")
def delete_transaction(transaction_id):
    if not finance_service().delete_transaction(transaction_id):
        abort(404)
    flash("Lançamento excluído. Você pode restaurá-lo na tela de Excluídos.", "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.get("/lancamentos/excluidos")
def deleted_transactions():
    return render_template("deleted_transactions.html", rows=finance_service().list_deleted_transactions())


@app.post("/lancamentos/<int:transaction_id>/restaurar")
def restore_transaction(transaction_id):
    if not finance_service().restore_transaction(transaction_id):
        abort(404)
    flash("Lançamento restaurado.", "success")
    return redirect(url_for("deleted_transactions"))


@app.post("/recorrencias/<int:rule_id>/alternar")
def toggle_recurrence(rule_id):
    finance_service().toggle_recurrence(rule_id)
    flash("Situação da recorrência atualizada.", "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.get("/projecao")
def projection():
    return render_template("projection.html", **finance_service().projection(date.today()))


if __name__ == "__main__":
    app.run(debug=True)
