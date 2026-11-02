"""Декоратор @log_action — трассировка доменных операций.

Пишет одну строку на вызов в логгер ``valutatrade.actions``::

    BUY user='alice' currency='BTC' amount=0.0500 rate=59337.21 base='USD' result=OK

Поля: action, user (или user_id), currency, amount, rate и base (если есть
в результате), result=OK/ERROR, error_type/error_message при исключении.
С verbose=True добавляется changes — состояние кошельков «было→стало».
Пароли и прочие аргументы в лог не попадают: берутся только поля из списка.
Исключения не глотаются — фиксируются в логе и пробрасываются дальше.
"""

import functools
import inspect
import logging
from collections.abc import Callable
from numbers import Real
from typing import Any, ParamSpec, TypeVar

from valutatrade_hub.core.exceptions import ValutaTradeError
from valutatrade_hub.core.utils import format_rate
from valutatrade_hub.logging_config import ACTIONS_LOGGER_NAME

P = ParamSpec("P")
R = TypeVar("R")

AMOUNT_DECIMALS = 4

logger = logging.getLogger(ACTIONS_LOGGER_NAME)


def _quote(value: Any) -> str:
    return f"'{value}'"


def _format_changes(changes: Any) -> str:
    return "; ".join(
        f"{c.currency_code} {c.before:.{AMOUNT_DECIMALS}f}→{c.after:.{AMOUNT_DECIMALS}f}"
        for c in changes
    )


def _collect_fields(params: dict[str, Any], result: Any, verbose: bool) -> dict[str, str]:
    """Собрать поля лога из аргументов вызова и результата операции."""
    fields: dict[str, str] = {}
    username = params.get("username") or getattr(result, "username", None)
    if username:
        fields["user"] = _quote(username)
    elif "user_id" in params:
        fields["user_id"] = str(params["user_id"])

    if params.get("currency_code") is not None:
        fields["currency"] = _quote(str(params["currency_code"]).upper())
    amount = params.get("amount")
    if isinstance(amount, Real) and not isinstance(amount, bool):
        fields["amount"] = f"{amount:.{AMOUNT_DECIMALS}f}"

    rate = getattr(result, "rate", None)
    if isinstance(rate, Real):
        fields["rate"] = format_rate(float(rate))
    base = getattr(result, "base_currency", None)
    if base:
        fields["base"] = _quote(base)
    if verbose and getattr(result, "changes", None):
        fields["changes"] = _quote(_format_changes(result.changes))
    return fields


def _render(action: str, fields: dict[str, str]) -> str:
    return " ".join([action, *(f"{key}={value}" for key, value in fields.items())])


def log_action(action: str, verbose: bool = False) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Логировать вызов доменной операции.

    Args:
        action: имя операции в логе (BUY, SELL, REGISTER, LOGIN).
        verbose: добавить состояние кошельков «было→стало».
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        signature = inspect.signature(func)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            params = dict(signature.bind_partial(*args, **kwargs).arguments)
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                fields = _collect_fields(params, None, verbose=False)
                fields["result"] = "ERROR"
                fields["error_type"] = type(exc).__name__
                fields["error_message"] = repr(str(exc))
                expected = isinstance(exc, ValutaTradeError | ValueError)
                log = logger.info if expected else logger.exception
                log(_render(action, fields))
                raise
            fields = _collect_fields(params, result, verbose)
            fields["result"] = "OK"
            logger.info(_render(action, fields))
            return result

        return wrapper

    return decorator
