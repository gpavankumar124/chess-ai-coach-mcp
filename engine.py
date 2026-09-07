"""Stockfish engine wrapper (via python-chess `chess.engine`).

Responsibilities:
  * find the Stockfish executable
  * keep ONE long-lived engine process (starting it is slow-ish; analysing is fast)
  * analyse a FEN and return a normalized result:
      - evaluation in centipawns, ALWAYS from White's point of view
      - forced-mate distance (signed: + = White mates, - = Black mates)
      - the best move as UCI and SAN, plus the principal variation

Engine analysis is for completed games / offline positions only.
"""

from __future__ import annotations

import atexit
import os
import shutil
import threading
from pathlib import Path

import chess
import chess.engine

from log import get_logger

_log = get_logger("engine")

# A mate is reported by python-chess as a huge cp number when you ask for one;
# we use this so "mate" always wins any comparison against a normal eval.
_MATE_CP = 100_000

_PROJECT_ENGINE_DIR = Path(__file__).resolve().parent / "engine"

# cached across calls
_engine_path: str | None = None
_engine: chess.engine.SimpleEngine | None = None


class EngineError(Exception):
    """Raised when Stockfish is missing or analysis fails."""


def find_stockfish() -> str:
    """Locate the Stockfish executable. Raises EngineError if not found.

    Search order:
      1. STOCKFISH_PATH environment variable
      2. a `stockfish*` executable in the project's engine/ folder
      3. `stockfish` on the system PATH
    """
    global _engine_path
    if _engine_path and Path(_engine_path).exists():
        return _engine_path

    env = os.environ.get("STOCKFISH_PATH")
    if env and Path(env).exists():
        _engine_path = env
        return env

    if _PROJECT_ENGINE_DIR.is_dir():
        for candidate in sorted(_PROJECT_ENGINE_DIR.glob("stockfish*")):
            if candidate.is_file() and candidate.suffix.lower() in (".exe", ""):
                _engine_path = str(candidate)
                return _engine_path

    on_path = shutil.which("stockfish")
    if on_path:
        _engine_path = on_path
        return on_path

    raise EngineError(
        "Stockfish executable not found. Put it in the project's engine/ folder "
        "or set the STOCKFISH_PATH environment variable."
    )


def _get_engine() -> chess.engine.SimpleEngine:
    """Return the shared engine process, starting it on first use."""
    global _engine
    if _engine is not None:
        return _engine
    path = find_stockfish()
    _log.info("starting Stockfish: %s", path)
    try:
        _engine = chess.engine.SimpleEngine.popen_uci(path)
        _engine.configure({"Threads": 2, "Hash": 64})
    except Exception as exc:  # noqa: BLE001 - surface any startup failure clearly
        _log.error("Stockfish failed to start (%s): %s", path, exc)
        raise EngineError(f"Could not start Stockfish ({path}): {exc}")
    _log.info("Stockfish ready: %s", _engine.id.get("name", "unknown"))
    return _engine


def close_engine() -> None:
    """Shut the engine process down.

    `SimpleEngine.quit()` can deadlock if called while the interpreter is already
    tearing down (e.g. from an atexit handler on Windows), so we run it in a
    thread and fall back to a hard close if it does not return quickly.
    """
    global _engine
    eng, _engine = _engine, None
    if eng is None:
        return
    _log.info("closing Stockfish")

    done = threading.Event()

    def _quit():
        try:
            eng.quit()
        except Exception:  # noqa: BLE001
            pass
        finally:
            done.set()

    threading.Thread(target=_quit, daemon=True).start()
    if not done.wait(timeout=3):
        try:
            eng.close()  # force-close transport + background loop
        except Exception:  # noqa: BLE001
            pass


atexit.register(close_engine)


def engine_available() -> dict:
    """Health check: is Stockfish usable? Returns details, never raises."""
    try:
        eng = _get_engine()
        return {
            "available": True,
            "path": _engine_path,
            "name": eng.id.get("name", "unknown"),
        }
    except EngineError as exc:
        return {"available": False, "error": str(exc)}


def _score_to_dict(pov_score: chess.engine.PovScore) -> dict:
    """Normalize a python-chess score to a White-perspective dict."""
    white_score = pov_score.white()  # Score from White's point of view
    mate = white_score.mate()        # None, or signed moves-to-mate
    if mate is not None:
        return {
            "cp": None,
            "mate": mate,
            "pawns": None,
            "string": f"#{'+' if mate > 0 else '-'}{abs(mate)}",
            "perspective": "white",
        }
    cp = white_score.score()         # centipawns, White POV
    return {
        "cp": cp,
        "mate": None,
        "pawns": round(cp / 100, 2),
        "string": f"{'+' if cp >= 0 else ''}{cp / 100:.2f}",
        "perspective": "white",
    }


def _pv_to_san(board: chess.Board, pv: list[chess.Move], limit: int = 12) -> list[str]:
    b = board.copy()
    out = []
    for mv in pv[:limit]:
        out.append(b.san(mv))
        b.push(mv)
    return out


def analyze_fen(fen: str, movetime: float = 0.2, multipv: int = 1) -> dict:
    """Analyse a position given by FEN.

    Args:
        fen: the position.
        movetime: seconds Stockfish may think (0.05 - 5).
        multipv: how many candidate lines to return (1 - 5).
    """
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise EngineError(f"Invalid FEN: {exc}")

    if board.is_game_over():
        return {
            "fen": board.fen(),
            "game_over": True,
            "outcome": board.result(),
            "eval": None,
            "best_move": None,
        }

    movetime = max(0.05, min(float(movetime), 5.0))
    multipv = max(1, min(int(multipv), 5))
    eng = _get_engine()

    try:
        info = eng.analyse(
            board, chess.engine.Limit(time=movetime), multipv=multipv
        )
    except chess.engine.EngineError as exc:
        raise EngineError(f"Stockfish analysis failed: {exc}")

    lines = info if isinstance(info, list) else [info]
    best = lines[0]
    best_pv = best.get("pv", [])

    result = {
        "fen": board.fen(),
        "game_over": False,
        "turn": "white" if board.turn == chess.WHITE else "black",
        "depth": best.get("depth"),
        "movetime_s": movetime,
        "eval": _score_to_dict(best["score"]),
        "best_move": None,
        "pv_san": _pv_to_san(board, best_pv) if best_pv else [],
    }
    if best_pv:
        result["best_move"] = {"uci": best_pv[0].uci(), "san": board.san(best_pv[0])}

    if multipv > 1:
        result["lines"] = []
        for ln in lines:
            pv = ln.get("pv", [])
            result["lines"].append({
                "eval": _score_to_dict(ln["score"]),
                "move": {"uci": pv[0].uci(), "san": board.san(pv[0])} if pv else None,
                "pv_san": _pv_to_san(board, pv) if pv else [],
            })

    return result
