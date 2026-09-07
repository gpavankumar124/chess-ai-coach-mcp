"""Chess.com Published-Data API client.

Plain functions that fetch data from https://api.chess.com/pub/ .
No API key is required, but Chess.com now expects a descriptive User-Agent
header, so we always send one.

Docs: https://www.chess.com/news/view/published-data-api
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests

import cache
from log import get_logger

_log = get_logger("chesscom")

BASE_URL = "https://api.chess.com/pub"

# How long a cached response stays "fresh" (seconds).
PROFILE_TTL = 24 * 3600
STATS_TTL = 6 * 3600
ARCHIVES_TTL = 6 * 3600
CURRENT_MONTH_TTL = 3600  # the current month is still gaining games

# Chess.com asks clients to identify themselves. A contact address is recommended.
HEADERS = {
    "User-Agent": (
        "chess-ai-coach-mcp/1.0 "
        "(https://github.com/gpavankumar124/chess-ai-coach-mcp; "
        "contact: gpavankumar124@gmail.com)"
    ),
    "Accept": "application/json",
}

# How long to wait (seconds) before giving up on a request.
TIMEOUT = 15


class ChessComError(Exception):
    """Raised for any problem talking to the Chess.com API.

    The message is safe to show to a user / the AI assistant.
    """


def _get(path: str) -> dict:
    """Send a GET request to `BASE_URL + path` and return the parsed JSON.

    Turns every failure (bad username, network error, rate limit, invalid JSON)
    into a single ChessComError with a readable message.
    """
    url = f"{BASE_URL}{path}"
    _log.info("GET %s", url)
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.exceptions.Timeout:
        raise ChessComError(f"Chess.com did not respond within {TIMEOUT}s (url: {url}).")
    except requests.exceptions.RequestException as exc:
        raise ChessComError(f"Network error contacting Chess.com: {exc}")

    if response.status_code == 404:
        raise ChessComError(f"Not found on Chess.com (url: {url}). Check the username.")
    if response.status_code == 429:
        raise ChessComError("Chess.com rate limit hit (HTTP 429). Wait a bit and try again.")
    if response.status_code >= 500:
        raise ChessComError(f"Chess.com server error (HTTP {response.status_code}). Try again later.")
    if response.status_code != 200:
        raise ChessComError(f"Unexpected response from Chess.com: HTTP {response.status_code}.")

    try:
        return response.json()
    except ValueError:
        raise ChessComError("Chess.com returned a response that was not valid JSON.")


def _clean_username(username: str) -> str:
    """Normalize a username: strip spaces, lowercase (Chess.com usernames are case-insensitive)."""
    if not username or not username.strip():
        raise ChessComError("Username is empty.")
    return username.strip().lower()


def get_player_profile(username: str, use_cache: bool = True) -> dict:
    """Return the player's public profile (country, join date, status, ...)."""
    user = _clean_username(username)
    if use_cache:
        hit = cache.load("profile", user, PROFILE_TTL)
        if hit is not None:
            return hit
    data = _get(f"/player/{user}")
    if use_cache:
        cache.save("profile", user, data)
    return data


def get_player_stats(username: str, use_cache: bool = True) -> dict:
    """Return the player's rating/record stats for every game type they play."""
    user = _clean_username(username)
    if use_cache:
        hit = cache.load("stats", user, STATS_TTL)
        if hit is not None:
            return hit
    data = _get(f"/player/{user}/stats")
    if use_cache:
        cache.save("stats", user, data)
    return data


def get_game_archives(username: str, use_cache: bool = True) -> list[str]:
    """Return the list of monthly archive URLs (one per month the player has games)."""
    user = _clean_username(username)
    if use_cache:
        hit = cache.load("archives", user, ARCHIVES_TTL)
        if hit is not None:
            return hit
    data = _get(f"/player/{user}/games/archives")
    archives = data.get("archives", [])
    if use_cache:
        cache.save("archives", user, archives)
    return archives


def get_games_for_month(
    username: str, year: int, month: int, use_cache: bool = True
) -> list[dict]:
    """Return every game the player finished in the given year/month.

    `month` is 1-12; it is zero-padded to two digits for the API.

    A month that is already over never changes, so it is cached forever. The
    current month is still gaining games, so its cache expires after 1 hour.
    """
    user = _clean_username(username)
    if not (1 <= month <= 12):
        raise ChessComError(f"Month must be between 1 and 12, got {month}.")
    if year < 2007:
        raise ChessComError(f"Chess.com has no archives before 2007, got {year}.")

    key = f"{user}/{year:04d}/{month:02d}"
    now = datetime.now(timezone.utc)
    is_past_month = (year, month) < (now.year, now.month)
    ttl = None if is_past_month else CURRENT_MONTH_TTL

    if use_cache:
        hit = cache.load("month_games", key, ttl)
        if hit is not None:
            return hit
    data = _get(f"/player/{user}/games/{year:04d}/{month:02d}")
    games = data.get("games", [])
    if use_cache:
        cache.save("month_games", key, games)
    return games
