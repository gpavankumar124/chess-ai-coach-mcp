"""Project-wide logging.

The MCP protocol uses **stdout**, so every log line MUST go to stderr - never
`print()` from library code. A rotating copy is also written to
``data/chess_coach.log``.

Set the level with the ``CHESS_COACH_LOG_LEVEL`` environment variable
(``DEBUG`` / ``INFO`` / ``WARNING`` / ``ERROR``; default ``INFO``).
"""

from __future__ import annotations

import functools
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent / "data"
_LOG_FILE = _LOG_DIR / "chess_coach.log"
_ROOT_NAME = "chess_coach"
_configured = False


def _configure() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    level = getattr(
        logging, os.environ.get("CHESS_COACH_LOG_LEVEL", "INFO").upper(), logging.INFO
    )
    root = logging.getLogger(_ROOT_NAME)
    root.setLevel(level)
    root.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S"
    )

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(fmt)
    root.addHandler(stderr_handler)

    try:  # file logging is best-effort - never break the server over it
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            _LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        pass


def get_logger(name: str = _ROOT_NAME) -> logging.Logger:
    """Return a configured logger. `name` is dotted under 'chess_coach'."""
    _configure()
    if name != _ROOT_NAME and not name.startswith(_ROOT_NAME + "."):
        name = f"{_ROOT_NAME}.{name}"
    return logging.getLogger(name)


def _preview(value: object, limit: int = 200) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "..."


def logged(func):
    """Decorator: log an MCP tool call, its duration, and whether it errored.

    Preserves the wrapped function's signature so FastMCP still sees the real
    parameters.
    """
    tool_log = get_logger("tools")

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        tool_log.info("call %s(%s)", func.__name__, _preview(kwargs))
        start = time.perf_counter()
        try:
            result = func(*args, **kwargs)
        except Exception:
            tool_log.exception("call %s raised", func.__name__)
            raise
        elapsed = time.perf_counter() - start
        if isinstance(result, dict) and "error" in result:
            tool_log.warning("call %s -> error in %.2fs: %s",
                             func.__name__, elapsed, result["error"])
        else:
            tool_log.info("call %s -> ok in %.2fs", func.__name__, elapsed)
        return result

    return wrapper
