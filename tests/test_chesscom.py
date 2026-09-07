"""Network tests for chesscom.py. Run with:  pytest -m network"""

import pytest

import chesscom

pytestmark = pytest.mark.network

USER = "hikaru"


def test_player_profile():
    profile = chesscom.get_player_profile(USER)
    assert profile.get("username", "").lower() == USER
    assert "joined" in profile


def test_player_stats():
    stats = chesscom.get_player_stats(USER)
    assert "chess_blitz" in stats


def test_game_archives_are_ordered_month_urls():
    archives = chesscom.get_game_archives(USER)
    assert archives and archives[0].endswith(("/01", "/02", "/03", "/04", "/05", "/06",
                                              "/07", "/08", "/09", "/10", "/11", "/12"))
    assert archives == sorted(archives)


def test_games_for_month_have_pgn():
    archives = chesscom.get_game_archives(USER)
    year, month = (int(x) for x in archives[-1].rstrip("/").split("/")[-2:])
    games = chesscom.get_games_for_month(USER, year, month)
    assert games and "pgn" in games[0]


def test_unknown_username_raises():
    with pytest.raises(chesscom.ChessComError):
        chesscom.get_player_profile("no_such_user_xyz_12345")


def test_bad_month_raises():
    with pytest.raises(chesscom.ChessComError):
        chesscom.get_games_for_month(USER, 2024, 13)
