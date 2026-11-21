"""What a background job may say about an error in its log and in jobs.last_error.

A database error's text carries the row it failed on: Postgres puts "Failing row contains
(...)" in its DETAIL, whatever the engine does with bound parameters. For a note or a memo
that row is the buyer's words, which must stay behind the board's access checks. So a
database error is reduced to its class and SQLSTATE, with no traceback; any other error is
logged in full.
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import StatementError


def _from_database(error: BaseException) -> bool:
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, StatementError):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def safe_error(error: BaseException) -> str:
    if isinstance(error, StatementError):
        code = getattr(error.orig, "sqlstate", None)
        return f"{type(error).__name__} (SQLSTATE {code})" if code else type(error).__name__
    if _from_database(error):
        return type(error).__name__
    return f"{type(error).__name__}: {error}"[:2000]


def log_failure(log: logging.Logger, error: BaseException, message: str, *args: object) -> None:
    """log.exception, except that an error from the database, or raised while handling one,
    is logged as its safe summary alone."""
    if _from_database(error):
        log.error(f"{message}: %s", *args, safe_error(error))
    else:
        log.exception(message, *args)
