"""Chess AI Coach MCP Server.

An MCP server that exposes tools for fetching and analyzing Chess.com data
so an AI assistant (e.g. Claude Desktop) can act as a chess coach.

Every tool returns its data on success, or ``{"error": "<message>"}`` on
failure - tools never raise to the MCP client.
"""

import hashlib
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastmcp import FastMCP

import analytics
import cache
import chesscom
import coaching
import engine
import games
import review
from log import get_logger, logged

_log = get_logger("server")


@asynccontextmanager
async def _lifespan(_server: FastMCP):
    """Start-up / shut-down hook: make sure Stockfish is closed cleanly on exit."""
    _log.info("Chess AI Coach MCP server starting")
    try:
        yield
    finally:
        engine.close_engine()
        _log.info("Chess AI Coach MCP server stopped")


# The MCP server instance. The name shows up in the MCP client (Claude Desktop).
mcp = FastMCP("Chess AI Coach", lifespan=_lifespan)


def tool(*args, **kwargs):
    """`@tool()` plus call/timing/error logging (see log.logged)."""
    def decorator(fn):
        return mcp.tool(*args, **kwargs)(logged(fn))
    return decorator

# Chess.com launched in 2007 - no game archives exist before then.
_EARLIEST_YEAR = 2007
_MAX_RECENT_GAMES = 50
# Analytics tools fetch whole monthly archives; cap how many to keep them fast.
_MAX_ANALYSIS_MONTHS = 12


# --------------------------------------------------------------------------- #
# Validation helpers - each returns an error string, or None if the input is OK #
# --------------------------------------------------------------------------- #


def _check_username(username: str) -> str | None:
    if not isinstance(username, str) or not username.strip():
        return "username must be a non-empty string."
    if len(username.strip()) > 50:
        return "username looks too long to be a real Chess.com username."
    return None


def _check_year_month(year: int, month: int) -> str | None:
    current_year = datetime.now(timezone.utc).year
    if not isinstance(year, int) or not (_EARLIEST_YEAR <= year <= current_year + 1):
        return f"year must be between {_EARLIEST_YEAR} and {current_year + 1}."
    if not isinstance(month, int) or not (1 <= month <= 12):
        return "month must be between 1 and 12."
    return None


def _check_limit(limit: int) -> str | None:
    if not isinstance(limit, int) or limit < 1:
        return "limit must be a positive integer."
    if limit > _MAX_RECENT_GAMES:
        return f"limit must be {_MAX_RECENT_GAMES} or fewer."
    return None


def _check_months(months: int) -> str | None:
    if not isinstance(months, int) or months < 1:
        return "months must be a positive integer."
    if months > _MAX_ANALYSIS_MONTHS:
        return f"months must be {_MAX_ANALYSIS_MONTHS} or fewer (one API call per month)."
    return None


def _collect_recent_months(username: str, months: int) -> list[dict] | dict:
    """Fetch and concatenate the raw games from the last `months` monthly archives.

    Returns the combined list of raw game dicts, or an {"error": ...} dict.
    """
    try:
        archives = chesscom.get_game_archives(username)
    except chesscom.ChessComError as exc:
        return {"error": str(exc)}

    games: list[dict] = []
    for url in list(reversed(archives))[:months]:
        year, month = (int(part) for part in url.rstrip("/").split("/")[-2:])
        try:
            games.extend(chesscom.get_games_for_month(username, year, month))
        except chesscom.ChessComError as exc:
            return {"error": str(exc)}
    return games


def _summarize_game(game: dict) -> dict:
    """Turn a raw Chess.com game object into a compact summary (no full PGN)."""
    white = game.get("white", {})
    black = game.get("black", {})
    return {
        "url": game.get("url"),
        "time_class": game.get("time_class"),
        "rated": game.get("rated"),
        "rules": game.get("rules"),
        "end_time": game.get("end_time"),
        "white": {"username": white.get("username"), "rating": white.get("rating"), "result": white.get("result")},
        "black": {"username": black.get("username"), "rating": black.get("rating"), "result": black.get("result")},
    }


@tool()
def ping(message: str = "hello") -> str:
    """Simple health-check tool. Returns a confirmation string.

    Use this to verify the Chess AI Coach MCP server is running and reachable.

    Args:
        message: Any text you want echoed back.
    """
    return f"Chess AI Coach MCP server is alive. You said: {message!r}"


@tool()
def server_info() -> dict:
    """Return basic information about this MCP server and its current phase."""
    return {
        "name": "Chess AI Coach",
        "version": "1.0.0",
        "phase": 10,
        "status": "complete - logging, tests, docs",
        "planned_features": [
            "player profile & stats",
            "recent games / games by month",
            "win-loss-draw stats",
            "white vs black performance",
            "opening analysis",
            "rating progress",
            "Stockfish post-game analysis",
            "mistake & weakness detection",
            "coaching report",
        ],
    }


# --------------------------------------------------------------------------- #
# Core Chess.com data tools (Phase 3)                                          #
# --------------------------------------------------------------------------- #


@tool()
def get_player_profile(username: str) -> dict:
    """Get a Chess.com player's public profile.

    Includes real name (if public), country, join date, follower count,
    league, and account status.

    Args:
        username: Chess.com username (case-insensitive).
    """
    error = _check_username(username)
    if error:
        return {"error": error}
    try:
        return chesscom.get_player_profile(username)
    except chesscom.ChessComError as exc:
        return {"error": str(exc)}


@tool()
def get_player_stats(username: str) -> dict:
    """Get a Chess.com player's rating and win/loss/draw record for each game type.

    Covers chess_blitz, chess_rapid, chess_bullet, chess_daily, tactics, puzzle
    rush, etc. (only the categories the player actually plays are present).

    Args:
        username: Chess.com username (case-insensitive).
    """
    error = _check_username(username)
    if error:
        return {"error": error}
    try:
        return chesscom.get_player_stats(username)
    except chesscom.ChessComError as exc:
        return {"error": str(exc)}


@tool()
def get_game_archives(username: str) -> dict:
    """List every month for which the player has completed games on Chess.com.

    Returns the months as "YYYY/MM" strings, plus the earliest and latest.
    Use this to decide which month to pass to get_games.

    Args:
        username: Chess.com username (case-insensitive).
    """
    error = _check_username(username)
    if error:
        return {"error": error}
    try:
        archives = chesscom.get_game_archives(username)
    except chesscom.ChessComError as exc:
        return {"error": str(exc)}
    months = ["/".join(url.rstrip("/").split("/")[-2:]) for url in archives]
    return {
        "count": len(months),
        "earliest": months[0] if months else None,
        "latest": months[-1] if months else None,
        "months": months,
    }


@tool()
def get_games(username: str, year: int, month: int) -> dict:
    """Get compact summaries of every game a player finished in a given month.

    Does NOT include the full PGN move list (that comes in a later analysis
    tool) - just result, ratings, time class and the game URL.

    Args:
        username: Chess.com username (case-insensitive).
        year: 4-digit year, e.g. 2026.
        month: month number 1-12.
    """
    error = _check_username(username) or _check_year_month(year, month)
    if error:
        return {"error": error}
    try:
        games = chesscom.get_games_for_month(username, year, month)
    except chesscom.ChessComError as exc:
        return {"error": str(exc)}
    return {
        "month": f"{year:04d}/{month:02d}",
        "count": len(games),
        "games": [_summarize_game(g) for g in games],
    }


@tool()
def get_recent_games(username: str, limit: int = 10) -> dict:
    """Get the player's most recently finished games (newest first).

    Walks backwards through the monthly archives until it has `limit` games
    or runs out of history. Returns compact summaries (no full PGN).

    Args:
        username: Chess.com username (case-insensitive).
        limit: how many recent games to return (1-50, default 10).
    """
    error = _check_username(username) or _check_limit(limit)
    if error:
        return {"error": error}

    try:
        archives = chesscom.get_game_archives(username)
    except chesscom.ChessComError as exc:
        return {"error": str(exc)}

    if not archives:
        return {"requested": limit, "count": 0, "games": []}

    collected: list[dict] = []
    # Newest archive URLs are last in the list -> iterate in reverse.
    for url in reversed(archives):
        year, month = (int(part) for part in url.rstrip("/").split("/")[-2:])
        try:
            month_games = chesscom.get_games_for_month(username, year, month)
        except chesscom.ChessComError as exc:
            return {"error": str(exc)}
        collected.extend(month_games)
        if len(collected) >= limit:
            break

    # end_time is a unix timestamp; sort newest first and keep `limit`.
    collected.sort(key=lambda g: g.get("end_time", 0), reverse=True)
    newest = collected[:limit]

    return {
        "requested": limit,
        "count": len(newest),
        "games": [_summarize_game(g) for g in newest],
    }


# --------------------------------------------------------------------------- #
# Analytics tools (Phase 4)                                                    #
# --------------------------------------------------------------------------- #


@tool()
def calculate_win_rate(username: str, months: int = 1, time_class: str | None = None) -> dict:
    """Win / loss / draw record and win rate for a player's recent games.

    Args:
        username: Chess.com username (case-insensitive).
        months: how many recent months of games to include (1-12, default 1).
        time_class: optional filter - "bullet", "blitz", "rapid" or "daily".
    """
    error = _check_username(username) or _check_months(months)
    if error:
        return {"error": error}
    games = _collect_recent_months(username, months)
    if isinstance(games, dict):
        return games  # error dict
    record = analytics.win_loss_draw(games, username, time_class)
    return {"username": username, "months": months, "time_class": time_class or "all", **record}


@tool()
def analyze_color_performance(username: str, months: int = 1, time_class: str | None = None) -> dict:
    """Compare how the player performs as White vs as Black in recent games.

    Args:
        username: Chess.com username (case-insensitive).
        months: how many recent months of games to include (1-12, default 1).
        time_class: optional filter - "bullet", "blitz", "rapid" or "daily".
    """
    error = _check_username(username) or _check_months(months)
    if error:
        return {"error": error}
    games = _collect_recent_months(username, months)
    if isinstance(games, dict):
        return games
    perf = analytics.color_performance(games, username, time_class)
    return {"username": username, "months": months, "time_class": time_class or "all", **perf}


@tool()
def analyze_openings(username: str, months: int = 3, min_games: int = 3) -> dict:
    """Break down the player's results by opening family (e.g. "Sicilian Defense").

    Openings with fewer than `min_games` games are dropped - a 1-game sample is
    not a trend.

    Args:
        username: Chess.com username (case-insensitive).
        months: how many recent months of games to include (1-12, default 3).
        min_games: minimum games for an opening to be reported (default 3).
    """
    error = _check_username(username) or _check_months(months)
    if error:
        return {"error": error}
    if not isinstance(min_games, int) or min_games < 1:
        return {"error": "min_games must be a positive integer."}
    games = _collect_recent_months(username, months)
    if isinstance(games, dict):
        return games
    openings = analytics.opening_performance(games, username, min_games=min_games)
    return {
        "username": username,
        "months": months,
        "min_games": min_games,
        "openings_reported": len(openings),
        "openings": openings,
    }


@tool()
def get_rating_progress(username: str, months: int = 3) -> dict:
    """Track how the player's rating moved over recent months, per time class.

    Args:
        username: Chess.com username (case-insensitive).
        months: how many recent months of games to include (1-12, default 3).
    """
    error = _check_username(username) or _check_months(months)
    if error:
        return {"error": error}
    games = _collect_recent_months(username, months)
    if isinstance(games, dict):
        return games
    progress = analytics.rating_progress(games, username)
    return {"username": username, "months": months, "by_time_class": progress}


# --------------------------------------------------------------------------- #
# python-chess tools (Phase 6)                                                 #
# --------------------------------------------------------------------------- #


def _nth_recent_raw_game(username: str, index: int) -> dict:
    """Return the raw game dict at `index` (0 = most recent) or an {"error": ...}."""
    try:
        archives = chesscom.get_game_archives(username)
    except chesscom.ChessComError as exc:
        return {"error": str(exc)}
    if not archives:
        return {"error": f"{username} has no games on Chess.com."}

    collected: list[dict] = []
    for url in reversed(archives):
        year, month = (int(part) for part in url.rstrip("/").split("/")[-2:])
        try:
            month_games = chesscom.get_games_for_month(username, year, month)
        except chesscom.ChessComError as exc:
            return {"error": str(exc)}
        collected.extend(reversed(month_games))  # newest first within the month
        if len(collected) > index:
            break

    if index >= len(collected):
        return {"error": f"{username} has only {len(collected)} games available; index {index} is out of range."}
    return collected[index]


@tool()
def get_game_pgn(username: str, year: int, month: int, index: int = -1) -> dict:
    """Get the full PGN (move list) of one specific game from a given month.

    Args:
        username: Chess.com username.
        year: 4-digit year.
        month: month number 1-12.
        index: which game in that month (0 = first, -1 = last, negatives allowed).
    """
    error = _check_username(username) or _check_year_month(year, month)
    if error:
        return {"error": error}
    try:
        month_games = chesscom.get_games_for_month(username, year, month)
    except chesscom.ChessComError as exc:
        return {"error": str(exc)}
    if not month_games:
        return {"error": f"No games for {username} in {year:04d}/{month:02d}."}
    try:
        game = month_games[index]
    except IndexError:
        return {"error": f"index {index} out of range (month has {len(month_games)} games)."}
    return {"summary": _summarize_game(game), "pgn": game.get("pgn", "")}


@tool()
def get_recent_game_pgn(username: str, index: int = 0) -> dict:
    """Get the full PGN of one of the player's most recent games.

    Args:
        username: Chess.com username.
        index: 0 = most recent game, 1 = the one before, ... (0-49).
    """
    error = _check_username(username)
    if error:
        return {"error": error}
    if not isinstance(index, int) or not (0 <= index <= 49):
        return {"error": "index must be between 0 and 49."}
    game = _nth_recent_raw_game(username, index)
    if "error" in game:
        return game
    return {"summary": _summarize_game(game), "pgn": game.get("pgn", "")}


@tool()
def replay_game(pgn: str, include_fens: bool = False, max_moves: int = 200) -> dict:
    """Parse a PGN and replay it move by move.

    Returns the headers plus every move as SAN ("Nf3") and UCI ("g1f3"), the
    final position (FEN) and the result.

    Args:
        pgn: the full PGN text of a completed game.
        include_fens: also return the FEN before and after each move.
        max_moves: stop after this many half-moves (default 200).
    """
    if not isinstance(max_moves, int) or not (1 <= max_moves <= 600):
        return {"error": "max_moves must be between 1 and 600."}
    try:
        return games.replay(pgn, include_fens=include_fens, max_moves=max_moves)
    except ValueError as exc:
        return {"error": str(exc)}


@tool()
def describe_position(fen: str) -> dict:
    """Describe a chess position given as FEN.

    Returns whose turn it is, the list of legal moves (SAN), check / checkmate /
    stalemate flags, a rough material balance and an ASCII board.

    Args:
        fen: a FEN string, e.g.
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1".
    """
    if not isinstance(fen, str) or not fen.strip():
        return {"error": "fen must be a non-empty string."}
    try:
        return games.describe_position(fen)
    except ValueError as exc:
        return {"error": str(exc)}


@tool()
def position_after_ply(pgn: str, ply: int) -> dict:
    """Replay a game up to `ply` half-moves and describe the resulting position.

    ply=0 is the start; ply=1 is after White's 1st move; ply=20 is after move 10
    for both sides.

    Args:
        pgn: the full PGN text of a completed game.
        ply: how many half-moves to play (0 or more).
    """
    if not isinstance(ply, int) or ply < 0:
        return {"error": "ply must be 0 or greater."}
    try:
        return games.position_after_ply(pgn, ply)
    except ValueError as exc:
        return {"error": str(exc)}


# --------------------------------------------------------------------------- #
# Stockfish engine tools (Phase 7)                                             #
# --------------------------------------------------------------------------- #


@tool()
def engine_status() -> dict:
    """Check whether the Stockfish chess engine is installed and working.

    Returns the engine name/path if available, or an error explaining how to
    install it.
    """
    return engine.engine_available()


@tool()
def analyze_position(fen: str, movetime: float = 0.3, multipv: int = 1) -> dict:
    """Evaluate a chess position with Stockfish.

    The evaluation is always from White's point of view:
      positive = White is better, negative = Black is better.
      "eval.cp" is in centipawns (100 = one pawn); "eval.mate" is set instead
      when there is a forced mate (e.g. 3 = White mates in 3, -2 = Black mates
      in 2).

    Args:
        fen: the position as a FEN string.
        movetime: seconds Stockfish may think, 0.05 - 5 (default 0.3).
        multipv: number of candidate moves to return, 1 - 5 (default 1).
    """
    if not isinstance(fen, str) or not fen.strip():
        return {"error": "fen must be a non-empty string."}
    try:
        return engine.analyze_fen(fen, movetime=movetime, multipv=multipv)
    except engine.EngineError as exc:
        return {"error": str(exc)}


@tool()
def best_move(fen: str, movetime: float = 0.3) -> dict:
    """Ask Stockfish for the best move in a position.

    Returns the move (UCI and SAN), the resulting evaluation (White's point of
    view) and the short line Stockfish expects to follow.

    Args:
        fen: the position as a FEN string.
        movetime: seconds Stockfish may think, 0.05 - 5 (default 0.3).
    """
    if not isinstance(fen, str) or not fen.strip():
        return {"error": "fen must be a non-empty string."}
    try:
        result = engine.analyze_fen(fen, movetime=movetime)
    except engine.EngineError as exc:
        return {"error": str(exc)}
    if result.get("game_over"):
        return {"fen": result["fen"], "game_over": True, "outcome": result["outcome"]}
    return {
        "fen": result["fen"],
        "turn": result["turn"],
        "best_move": result["best_move"],
        "eval": result["eval"],
        "line": result["pv_san"],
        "depth": result["depth"],
    }


# --------------------------------------------------------------------------- #
# Whole-game analysis tools (Phase 8)                                          #
# --------------------------------------------------------------------------- #

_LINK_RE = re.compile(r'\[Link\s+"([^"]+)"\]')


def _review_cache_key(pgn: str, movetime: float, max_plies: int) -> str:
    """Stable cache key for a game review: the game URL if present, else a hash."""
    match = _LINK_RE.search(pgn)
    base = match.group(1) if match else "sha1:" + hashlib.sha1(pgn.encode("utf-8")).hexdigest()
    return f"v{review.SCHEMA_VERSION}|{base}|mt={movetime}|mp={max_plies}"


def _cached_review(pgn: str, movetime: float, max_plies: int) -> dict:
    """review.review_game(...) with a permanent cache (engine work is expensive)."""
    key = _review_cache_key(pgn, movetime, max_plies)
    hit = cache.load("review", key, None)
    if hit is not None:
        _log.info("review cache hit: %s", key)
        hit["cached"] = True
        return hit
    _log.info("review cache miss: %s", key)
    result = review.review_game(pgn, movetime=movetime, max_plies=max_plies)
    cache.save("review", key, result)
    result["cached"] = False
    return result


def _tag_reviewed_player(result: dict, username: str | None) -> dict:
    """Mark which side the requested player was and surface their summary."""
    if not username:
        return result
    user = username.strip().lower()
    white = (result["game"].get("white") or "").lower()
    black = (result["game"].get("black") or "").lower()
    side = "white" if user == white else "black" if user == black else None
    result["reviewed_player"] = side
    if side:
        result["your_summary"] = result["players"][side]
    return result


@tool()
def analyze_game(pgn: str, movetime: float = 0.15, max_plies: int = 120,
                 username: str | None = None) -> dict:
    """Analyse a whole finished game with Stockfish and label every move.

    Returns per-move evaluations and a classification for each move
    (brilliant / great / best / good / book / inaccuracy / mistake / blunder /
    forced), an accuracy % and average centipawn loss for each player, and the
    game's key moments. Results are cached.

    This runs one engine analysis per position, so a long game at a high
    movetime can take a while. Analysis is for completed games only.

    Args:
        pgn: the full PGN text of a finished game.
        movetime: seconds Stockfish thinks per position, 0.05 - 1.0 (default 0.15).
        max_plies: stop after this many half-moves, 2 - 300 (default 120).
        username: optional - whose side to highlight in the summary.
    """
    if not isinstance(pgn, str) or not pgn.strip():
        return {"error": "pgn must be a non-empty string."}
    if not isinstance(movetime, (int, float)) or not (0.05 <= float(movetime) <= 1.0):
        return {"error": "movetime must be between 0.05 and 1.0 seconds."}
    if not isinstance(max_plies, int) or not (2 <= max_plies <= 300):
        return {"error": "max_plies must be between 2 and 300."}
    try:
        result = _cached_review(pgn, float(movetime), max_plies)
    except ValueError as exc:
        return {"error": str(exc)}
    except engine.EngineError as exc:
        return {"error": str(exc)}
    return _tag_reviewed_player(result, username)


@tool()
def review_recent_game(username: str, index: int = 0, movetime: float = 0.15,
                       max_plies: int = 120) -> dict:
    """Fetch one of a player's recent games and run a full Stockfish review of it.

    Convenience wrapper around get_recent_game_pgn + analyze_game. The requested
    player's side is highlighted in the result ("your_summary").

    Args:
        username: Chess.com username.
        index: 0 = most recent game, 1 = the one before, ... (0-49).
        movetime: seconds Stockfish thinks per position, 0.05 - 1.0 (default 0.15).
        max_plies: stop after this many half-moves, 2 - 300 (default 120).
    """
    error = _check_username(username)
    if error:
        return {"error": error}
    if not isinstance(index, int) or not (0 <= index <= 49):
        return {"error": "index must be between 0 and 49."}
    if not isinstance(movetime, (int, float)) or not (0.05 <= float(movetime) <= 1.0):
        return {"error": "movetime must be between 0.05 and 1.0 seconds."}
    if not isinstance(max_plies, int) or not (2 <= max_plies <= 300):
        return {"error": "max_plies must be between 2 and 300."}

    game = _nth_recent_raw_game(username, index)
    if "error" in game:
        return game
    pgn = game.get("pgn", "")
    if not pgn.strip():
        return {"error": "That game has no PGN to analyse."}
    try:
        result = _cached_review(pgn, float(movetime), max_plies)
    except ValueError as exc:
        return {"error": str(exc)}
    except engine.EngineError as exc:
        return {"error": str(exc)}
    return _tag_reviewed_player(result, username)


# --------------------------------------------------------------------------- #
# AI coaching tools (Phase 9)                                                  #
# --------------------------------------------------------------------------- #

_MAX_COACH_GAMES = 15


def _recent_raw_games(username: str, count: int) -> list[dict] | dict:
    """Newest `count` raw game dicts (with PGN), newest first, or {"error": ...}."""
    try:
        archives = chesscom.get_game_archives(username)
    except chesscom.ChessComError as exc:
        return {"error": str(exc)}
    if not archives:
        return {"error": f"{username} has no games on Chess.com."}

    collected: list[dict] = []
    for url in reversed(archives):
        year, month = (int(part) for part in url.rstrip("/").split("/")[-2:])
        try:
            month_games = chesscom.get_games_for_month(username, year, month)
        except chesscom.ChessComError as exc:
            return {"error": str(exc)}
        collected.extend(reversed(month_games))
        if len(collected) >= count:
            break
    return collected[:count]


def _collect_reviews(username: str, num_games: int, movetime: float, max_plies: int):
    """Fetch + review the player's recent games. Returns (pairs, meta) or {"error": ...}.

    pairs is a list of (review_result, "white"|"black").
    """
    raw = _recent_raw_games(username, num_games)
    if isinstance(raw, dict):
        return raw

    _log.info("collecting reviews for %s: %d games, movetime=%.2fs, max_plies=%d",
              username, len(raw), movetime, max_plies)
    user = username.strip().lower()
    pairs: list[tuple[dict, str]] = []
    skipped = 0
    truncated = 0
    for g in raw:
        pgn = g.get("pgn", "") or ""
        if g.get("rules") != "chess" or not pgn.strip():
            skipped += 1
            continue
        white = (g.get("white", {}).get("username", "") or "").lower()
        black = (g.get("black", {}).get("username", "") or "").lower()
        side = "white" if white == user else "black" if black == user else None
        if side is None:
            skipped += 1
            continue
        try:
            result = _cached_review(pgn, movetime, max_plies)
        except (ValueError, engine.EngineError) as exc:
            return {"error": f"Game analysis failed: {exc}"}
        if result.get("game", {}).get("truncated"):
            truncated += 1
        pairs.append((result, side))

    if not pairs:
        return {"error": f"No analyzable standard-chess games found for {username}."}

    meta = {
        "games_requested": num_games,
        "games_analyzed": len(pairs),
        "games_skipped": skipped,
        "partly_analyzed_games": truncated,
        "movetime_s": movetime,
        "max_plies": max_plies,
        "note": "Each game review is cached; running again (same settings) is fast.",
    }
    return pairs, meta


def _check_coach_args(num_games, movetime, max_plies) -> str | None:
    if not isinstance(num_games, int) or not (1 <= num_games <= _MAX_COACH_GAMES):
        return f"num_games must be between 1 and {_MAX_COACH_GAMES}."
    if not isinstance(movetime, (int, float)) or not (0.05 <= float(movetime) <= 0.5):
        return "movetime must be between 0.05 and 0.5 seconds."
    if not isinstance(max_plies, int) or not (20 <= max_plies <= 150):
        return "max_plies must be between 20 and 150."
    return None


@tool()
def find_biggest_mistakes(username: str, num_games: int = 6, top: int = 10,
                          movetime: float = 0.1, max_plies: int = 80) -> dict:
    """Find a player's worst moves across their recent games, ranked by how much
    evaluation they threw away (centipawn loss).

    Reviews each recent game with Stockfish (cached), then lists the biggest
    blunders and mistakes with the move played, the better move, and the eval
    swing.

    First run is slow (one engine pass per game); later runs are fast.

    Args:
        username: Chess.com username.
        num_games: how many recent games to analyze, 1-15 (default 6).
        top: how many mistakes to return, 1-30 (default 10).
        movetime: engine seconds per position, 0.05-0.5 (default 0.1).
        max_plies: half-moves analyzed per game, 20-150 (default 80).
    """
    err = _check_username(username) or _check_coach_args(num_games, movetime, max_plies)
    if err:
        return {"error": err}
    if not isinstance(top, int) or not (1 <= top <= 30):
        return {"error": "top must be between 1 and 30."}
    got = _collect_reviews(username, num_games, float(movetime), max_plies)
    if isinstance(got, dict):
        return got
    pairs, meta = got
    return {
        "username": username,
        "scope": meta,
        "mistakes": coaching.biggest_mistakes(pairs, top=top),
    }


@tool()
def analyze_player_weaknesses(username: str, num_games: int = 8,
                              movetime: float = 0.1, max_plies: int = 90) -> dict:
    """Detect a player's recurring weaknesses from their recent games.

    Looks at accuracy by game phase (opening/middlegame/endgame), by colour,
    blunder timing, winning-position conversion and opening results. Every
    finding carries a `confidence` based on the sample size - a pattern from 4
    games is "tentative", one from 25 games is "strong".

    First run is slow (one engine pass per game); later runs are fast.

    Args:
        username: Chess.com username.
        num_games: how many recent games to analyze, 1-15 (default 8).
        movetime: engine seconds per position, 0.05-0.5 (default 0.1).
        max_plies: half-moves analyzed per game, 20-150 (default 90).
    """
    err = _check_username(username) or _check_coach_args(num_games, movetime, max_plies)
    if err:
        return {"error": err}
    got = _collect_reviews(username, num_games, float(movetime), max_plies)
    if isinstance(got, dict):
        return got
    pairs, meta = got
    w = coaching.weaknesses(pairs)
    return {
        "username": username,
        "scope": meta,
        "overall_confidence": w["overall_confidence"],
        "by_phase": coaching.phase_breakdown(pairs),
        "by_color": coaching.color_breakdown(pairs),
        "blunder_timing": coaching.blunder_timing(pairs),
        "conversion": coaching.conversion(pairs),
        "weaknesses": w["findings"],
    }


@tool()
def generate_coach_report(username: str, num_games: int = 8,
                          movetime: float = 0.1, max_plies: int = 90) -> dict:
    """Generate a full chess coaching report from a player's recent games.

    Combines everything: win/loss record, average accuracy, accuracy by phase
    and colour, opening results, winning-position conversion, the biggest
    mistakes, detected strengths and weaknesses (each with a confidence),
    concrete recommendations, and explicit caveats about sample size.

    First run is slow (one engine pass per game); later runs are fast.

    Args:
        username: Chess.com username.
        num_games: how many recent games to analyze, 1-15 (default 8).
        movetime: engine seconds per position, 0.05-0.5 (default 0.1).
        max_plies: half-moves analyzed per game, 20-150 (default 90).
    """
    err = _check_username(username) or _check_coach_args(num_games, movetime, max_plies)
    if err:
        return {"error": err}
    got = _collect_reviews(username, num_games, float(movetime), max_plies)
    if isinstance(got, dict):
        return got
    pairs, meta = got
    return coaching.build_report(pairs, username, meta)


# --------------------------------------------------------------------------- #
# Cache tools (Phase 5)                                                        #
# --------------------------------------------------------------------------- #

_CACHE_NAMESPACES = {"profile", "stats", "archives", "month_games", "review"}


@tool()
def cache_info() -> dict:
    """Show what the local SQLite cache currently holds (entries per type, size).

    The cache stores Chess.com API responses so repeated requests do not hit the
    network. Finished months are cached permanently; profiles/stats expire after
    a few hours.
    """
    return cache.stats()


@tool()
def clear_cache(namespace: str | None = None) -> dict:
    """Delete cached Chess.com data to force fresh fetches.

    Args:
        namespace: optionally clear only one type -
            "profile", "stats", "archives" or "month_games".
            Omit to clear everything.
    """
    if namespace is not None and namespace not in _CACHE_NAMESPACES:
        return {"error": f"namespace must be one of {sorted(_CACHE_NAMESPACES)} or omitted."}
    removed = cache.clear(namespace)
    return {"cleared": namespace or "all", "rows_removed": removed}


def main() -> None:
    """Entry point: run the MCP server over stdio (how Claude Desktop talks to it)."""
    mcp.run()


if __name__ == "__main__":
    main()
