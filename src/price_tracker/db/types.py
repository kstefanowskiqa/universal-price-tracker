from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

PRICE_QUANTUM = Decimal("0.0001")


class MoneyDecimal(TypeDecorator[Decimal]):
    """Przenośny typ kwotowy bez utraty precyzji.

    - PostgreSQL / inne RDBMS: natywny NUMERIC(14, 4)
    - SQLite: znormalizowany VARCHAR(32) chroniący przed konwersją do REAL (float)
    """

    impl = Numeric(14, 4)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(32))
        return dialect.type_descriptor(Numeric(14, 4))

    def process_bind_param(self, value: Decimal | int | str | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError(
                "Typ float jest zabroniony dla wartości finansowych; użyj decimal.Decimal."
            )
        try:
            dec = value if isinstance(value, Decimal) else Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"Nieprawidłowa wartość dziesiętna: {value!r}") from exc

        quantized = dec.quantize(PRICE_QUANTUM)
        if dialect.name == "sqlite":
            return format(quantized, "f")
        return quantized

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))


class UTCDateTime(TypeDecorator[datetime]):
    """Przenośny znacznik czasu wymuszający strefę UTC przy zapisie i odczycie."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "Naiwny obiekt datetime bez strefy czasowej jest zabroniony; użyj UTC."
            )
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
