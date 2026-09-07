"""Pure-logic tests for games.py (python-chess wrappers, no engine)."""

import chess
import pytest

import games

START_FEN = chess.STARTING_FEN
SCHOLARS_PGN = (
    '[Event "Test"]\n[White "A"]\n[Black "B"]\n[Result "1-0"]\n\n'
    "1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0\n"
)


def test_describe_starting_position():
    d = games.describe_position(START_FEN)
    assert d["turn"] == "white"
    assert d["num_legal_moves"] == 20
    assert d["material"]["balance"] == 0
    assert d["is_check"] is False


def test_invalid_fen_raises():
    with pytest.raises(ValueError):
        games.describe_position("not a fen")


def test_replay_scholars_mate():
    r = games.replay(SCHOLARS_PGN, include_fens=True)
    assert r["total_plies"] == 7
    assert r["result"] == "1-0"
    assert r["moves"][0]["san"] == "e4"
    assert r["moves"][0]["uci"] == "e2e4"
    assert r["moves"][-1]["san"] == "Qxf7#"


def test_final_position_is_checkmate():
    r = games.replay(SCHOLARS_PGN)
    assert games.describe_position(r["final_fen"])["is_checkmate"] is True


def test_parse_empty_pgn_raises():
    with pytest.raises(ValueError):
        games.parse_game("")


def test_position_after_ply():
    assert games.position_after_ply(SCHOLARS_PGN, 0)["fen"] == START_FEN
    p3 = games.position_after_ply(SCHOLARS_PGN, 3)  # 1.e4 e5 2.Bc4
    assert p3["last_move_san"] == "Bc4"
    assert p3["turn"] == "black"
