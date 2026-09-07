"""Pure analytics functions for Chess.com game data.

Everything here operates on plain Python data (lists of "game" dicts exactly as
Chess.com returns them) - no network, no MCP. That makes it easy to test.

A Chess.com game object looks like:
    {
      "url": "...", "pgn": "...", "time_class": "blitz", "rated": True,
      "rules": "chess", "end_time": 1788124673,
      "white": {"username": "Hikaru", "rating": 3370, "result": "win"},
      "black": {"username": "someone", "rating": 3300, "result": "resigned"},
      "eco": "https://www.chess.com/openings/Sicilian-Defense-...",   # optional
    }
"""

from __future__ import annotations

import re

# Chess.com uses one "result" string per side. One side is "win"; the other is
# the reason it ended. Draw reasons are shared by both sides.
DRAW_RESULTS = {
    "agreed", "repetition", "stalemate", "insufficient", "50move",
    "timevsinsufficient",
}
WIN_RESULT = "win"
# Anything not "win" and not a draw reason is treated as a loss
# (checkmated, timeout, resigned, lose, abandoned, kingofthehill, threecheck...).


def classify_result(side_result: str) -> str:
    """Map one side's Chess.com result string to 'win' | 'loss' | 'draw'."""
    if side_result == WIN_RESULT:
        return "win"
    if side_result in DRAW_RESULTS:
        return "draw"
    return "loss"


def player_side(game: dict, username: str) -> dict | None:
    """Return the game's 'white' or 'black' sub-dict for the requested player.

    Returns None if the username is not one of the two players.
    """
    target = username.strip().lower()
    white = game.get("white", {})
    black = game.get("black", {})
    if str(white.get("username", "")).lower() == target:
        return white
    if str(black.get("username", "")).lower() == target:
        return black
    return None


def player_color(game: dict, username: str) -> str | None:
    """Return 'white' or 'black' for the requested player, or None if not in game."""
    target = username.strip().lower()
    if str(game.get("white", {}).get("username", "")).lower() == target:
        return "white"
    if str(game.get("black", {}).get("username", "")).lower() == target:
        return "black"
    return None


def parse_pgn_headers(pgn: str) -> dict:
    """Extract the [Key "Value"] header lines from a PGN string into a dict."""
    if not pgn:
        return {}
    return dict(re.findall(r'\[(\w+)\s+"([^"]*)"\]', pgn))


def opening_family(game: dict) -> str:
    """Best-effort broad opening name, e.g. 'Sicilian Defense', 'Italian Game'.

    Prefers the ECOUrl from the PGN headers, falls back to the game's top-level
    'eco' field. Returns 'Unknown' when nothing is available.
    """
    headers = parse_pgn_headers(game.get("pgn", ""))
    url = headers.get("ECOUrl") or game.get("eco") or ""
    slug = url.rstrip("/").split("/")[-1]  # "Sicilian-Defense-Najdorf-Variation"
    if not slug:
        return "Unknown"
    words = slug.replace("-", " ").split()
    return " ".join(words[:2]) if words else "Unknown"


def _blank_record() -> dict:
    return {"games": 0, "wins": 0, "losses": 0, "draws": 0}


def _add_result(record: dict, outcome: str) -> None:
    record["games"] += 1
    record[{"win": "wins", "loss": "losses", "draw": "draws"}[outcome]] += 1


def _finalize(record: dict) -> dict:
    """Add win_rate and score_pct (draws count as half a point) to a record."""
    n = record["games"]
    record["win_rate"] = round(record["wins"] / n, 3) if n else 0.0
    record["score_pct"] = round((record["wins"] + 0.5 * record["draws"]) / n * 100, 1) if n else 0.0
    return record


def _relevant_games(games: list[dict], username: str, time_class: str | None) -> list[dict]:
    """Games the player actually took part in, standard chess only, optional time filter."""
    out = []
    for g in games:
        if g.get("rules") != "chess":
            continue
        if time_class and g.get("time_class") != time_class:
            continue
        if player_side(g, username) is None:
            continue
        out.append(g)
    return out


def win_loss_draw(games: list[dict], username: str, time_class: str | None = None) -> dict:
    """Overall win/loss/draw record for the player across `games`."""
    record = _blank_record()
    for g in _relevant_games(games, username, time_class):
        side = player_side(g, username)
        _add_result(record, classify_result(side.get("result", "")))
    return _finalize(record)


def color_performance(games: list[dict], username: str, time_class: str | None = None) -> dict:
    """Separate win/loss/draw records for the player as White and as Black."""
    result = {"white": _blank_record(), "black": _blank_record()}
    for g in _relevant_games(games, username, time_class):
        color = player_color(g, username)
        side = player_side(g, username)
        _add_result(result[color], classify_result(side.get("result", "")))
    result["white"] = _finalize(result["white"])
    result["black"] = _finalize(result["black"])
    return result


def opening_performance(
    games: list[dict], username: str, min_games: int = 3, top: int = 15
) -> list[dict]:
    """Per-opening-family record, from the player's perspective, sorted by frequency.

    Only families with at least `min_games` games are returned (a small sample
    tells you nothing), capped at `top` entries.
    """
    families: dict[str, dict] = {}
    for g in _relevant_games(games, username, time_class=None):
        fam = opening_family(g)
        rec = families.setdefault(fam, {**_blank_record(), "as_white": 0, "as_black": 0})
        side = player_side(g, username)
        _add_result(rec, classify_result(side.get("result", "")))
        rec["as_white" if player_color(g, username) == "white" else "as_black"] += 1

    ranked = sorted(families.items(), key=lambda kv: kv[1]["games"], reverse=True)
    out = []
    for name, rec in ranked:
        if rec["games"] < min_games or name == "Unknown":
            continue
        out.append({"opening": name, **_finalize(rec)})
        if len(out) >= top:
            break
    return out


def _downsample(points: list[dict], max_points: int = 40) -> list[dict]:
    """Keep at most `max_points` evenly-spaced points (always keep first and last)."""
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    picked = [points[int(i * step)] for i in range(max_points)]
    picked[-1] = points[-1]
    return picked


def rating_progress(games: list[dict], username: str) -> dict:
    """Rating trajectory per time class (standard chess, rated games only)."""
    by_class: dict[str, list[dict]] = {}
    for g in games:
        if g.get("rules") != "chess" or not g.get("rated"):
            continue
        side = player_side(g, username)
        if side is None or side.get("rating") is None:
            continue
        tc = g.get("time_class", "unknown")
        by_class.setdefault(tc, []).append(
            {"end_time": g.get("end_time", 0), "rating": side["rating"]}
        )

    summary = {}
    for tc, pts in by_class.items():
        pts.sort(key=lambda p: p["end_time"])
        ratings = [p["rating"] for p in pts]
        summary[tc] = {
            "num_games": len(pts),
            "first_rating": ratings[0],
            "last_rating": ratings[-1],
            "change": ratings[-1] - ratings[0],
            "peak": max(ratings),
            "low": min(ratings),
            "series": _downsample(pts),
        }
    return summary
