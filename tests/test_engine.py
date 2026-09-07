"""Stockfish tests. Auto-skipped if the engine is not installed."""

import chess
import pytest

import engine

pytestmark = pytest.mark.engine

START = chess.STARTING_FEN
MATE_IN_1 = "6k1/5ppp/8/8/8/8/8/R6K w - - 0 1"          # White: Ra8#
WHITE_DOWN_QUEEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNB1KBNR w KQkq - 0 1"
GAME_OVER = "R5k1/5ppp/8/8/8/8/8/6K1 b - - 0 1"


def test_engine_available(stockfish):
    status = engine.engine_available()
    assert status["available"] is True
    assert "Stockfish" in status["name"]


def test_starting_position_is_roughly_equal(stockfish):
    r = engine.analyze_fen(START, movetime=0.2)
    assert r["eval"]["mate"] is None
    assert -80 <= r["eval"]["cp"] <= 120
    assert r["best_move"]["san"] in {"e4", "d4", "Nf3", "c4", "g3", "e3", "b3"}


def test_mate_in_one_detected(stockfish):
    r = engine.analyze_fen(MATE_IN_1, movetime=0.2)
    assert r["eval"]["mate"] == 1
    assert r["best_move"]["san"].endswith("#")


def test_eval_sign_is_white_perspective(stockfish):
    r = engine.analyze_fen(WHITE_DOWN_QUEEN, movetime=0.2)
    assert r["eval"]["cp"] < -500  # Black is much better -> negative


def test_finished_position(stockfish):
    r = engine.analyze_fen(GAME_OVER, movetime=0.1)
    assert r["game_over"] is True


def test_invalid_fen_raises(stockfish):
    with pytest.raises(engine.EngineError):
        engine.analyze_fen("nonsense", movetime=0.1)


def test_multipv_returns_lines(stockfish):
    r = engine.analyze_fen(START, movetime=0.2, multipv=3)
    assert len(r.get("lines", [])) == 3
