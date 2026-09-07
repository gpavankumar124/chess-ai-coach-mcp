"""Whole-game analysis tests. Auto-skipped if Stockfish is not installed."""

import pytest

import review

pytestmark = pytest.mark.engine

SCHOLARS = (
    '[White "WhitePlayer"]\n[Black "BlackPlayer"]\n[Result "1-0"]\n\n'
    "1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0\n"
)


@pytest.fixture(scope="module")
def scholars_review(stockfish):
    return review.review_game(SCHOLARS, movetime=0.2)


def test_all_plies_scored(scholars_review):
    assert scholars_review["game"]["analyzed_plies"] == 7


def test_black_blunder_walks_into_mate(scholars_review):
    by_ply = {m["ply"]: m for m in scholars_review["moves"]}
    assert by_ply[6]["classification"] == "blunder"
    assert by_ply[6]["cpl"] >= 300


def test_mating_move_is_not_a_mistake(scholars_review):
    by_ply = {m["ply"]: m for m in scholars_review["moves"]}
    assert by_ply[7]["classification"] in ("best", "great", "brilliant", "forced")


def test_white_more_accurate_than_black(scholars_review):
    p = scholars_review["players"]
    assert p["white"]["accuracy"] > p["black"]["accuracy"]


def test_counts_sum_to_moves_played(scholars_review):
    for color in ("white", "black"):
        played = sum(1 for m in scholars_review["moves"] if m["color"] == color)
        assert sum(scholars_review["players"][color]["counts"].values()) == played


def test_every_move_has_phase_and_classification(scholars_review):
    for m in scholars_review["moves"]:
        assert m["phase"] in ("opening", "middlegame", "endgame")
        assert m["classification"] in review._LABELS


def test_empty_pgn_raises(stockfish):
    with pytest.raises(ValueError):
        review.review_game('[Event "x"]\n\n*\n', movetime=0.1)
