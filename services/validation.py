from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


def month_date(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def parse_amount(raw: str, *, require_positive: bool = True) -> float:
    clean = raw.strip().replace("R$", "").replace(".", "").replace(",", ".")
    amount = Decimal(clean).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if not amount.is_finite():
        raise ValueError("Informe um valor numérico válido.")
    if require_positive and amount <= 0:
        raise ValueError("Informe um valor maior que zero.")
    return float(amount)


def kind_label(kind: str) -> str:
    return "receita" if kind == "income" else "despesa"
