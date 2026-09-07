"""Pure-logic tests for analytics.py (no network)."""

import analytics

PGN_SICILIAN = (
    '[ECO "B90"]\n'
    '[ECOUrl "https://www.chess.com/openings/Sicilian-Defense-Najdorf-Variation"]\n\n'
    "1. e4 c5"
)
PGN_ITALIAN = '[ECOUrl "https://www.chess.com/openings/Italian-Game-Giuoco-Piano"]\n\n1. e4 e5'

FAKE_GAMES = [
    {"rules": "chess", "time_class": "blitz", "rated": True, "end_time": 100,
     "white": {"username": "Pavan", "rating": 1500, "result": "win"},
     "black": {"username": "opp1", "rating": 1490, "result": "resigned"},
     "pgn": PGN_SICILIAN},
    {"rules": "chess", "time_class": "blitz", "rated": True, "end_time": 200,
     "white": {"username": "opp2", "rating": 1520, "result": "win"},
     "black": {"username": "pavan", "rating": 1495, "result": "checkmated"},
     "pgn": PGN_SICILIAN},
    {"rules": "chess", "time_class": "blitz", "rated": True, "end_time": 300,
     "white": {"username": "PAVAN", "rating": 1505, "result": "agreed"},
     "black": {"username": "opp3", "rating": 1600, "result": "agreed"},
     "pgn": PGN_ITALIAN},
    {"rules": "chess", "time_class": "rapid", "rated": True, "end_time": 400,
     "white": {"username": "opp4", "rating": 1400, "result": "timeout"},
     "black": {"username": "Pavan", "rating": 1510, "result": "win"},
     "pgn": PGN_ITALIAN},
    # a variant game - must be ignored everywhere
    {"rules": "chess960", "time_class": "blitz", "rated": True, "end_time": 500,
     "white": {"username": "Pavan", "rating": 1, "result": "checkmated"},
     "black": {"username": "opp5", "rating": 1, "result": "win"}, "pgn": ""},
]


class TestClassifyResult:
    def test_win(self):
        assert analytics.classify_result("win") == "win"

    def test_loss_reasons(self):
        for r in ("checkmated", "timeout", "resigned", "abandoned", "lose"):
            assert analytics.classify_result(r) == "loss"

    def test_draw_reasons(self):
        for r in ("agreed", "repetition", "stalemate", "insufficient", "50move"):
            assert analytics.classify_result(r) == "draw"


class TestColorDetection:
    def test_case_insensitive(self):
        assert analytics.player_color(FAKE_GAMES[0], "pavan") == "white"
        assert analytics.player_color(FAKE_GAMES[1], "pavan") == "black"
        assert analytics.player_color(FAKE_GAMES[2], "pavan") == "white"

    def test_not_in_game(self):
        assert analytics.player_color(FAKE_GAMES[0], "someone_else") is None


def test_win_loss_draw_excludes_variants():
    wld = analytics.win_loss_draw(FAKE_GAMES, "pavan")
    assert (wld["games"], wld["wins"], wld["losses"], wld["draws"]) == (4, 2, 1, 1)
    assert wld["win_rate"] == 0.5
    assert wld["score_pct"] == 62.5


def test_color_performance_split():
    cp = analytics.color_performance(FAKE_GAMES, "pavan")
    assert cp["white"]["games"] == 2 and cp["white"]["wins"] == 1
    assert cp["black"]["games"] == 2 and cp["black"]["wins"] == 1


def test_opening_families_from_pgn():
    ops = analytics.opening_performance(FAKE_GAMES, "pavan", min_games=1)
    assert {o["opening"] for o in ops} == {"Sicilian Defense", "Italian Game"}


def test_rating_progress_per_time_class():
    rp = analytics.rating_progress(FAKE_GAMES, "pavan")
    assert rp["blitz"]["num_games"] == 3
    assert rp["blitz"]["first_rating"] == 1500
    assert rp["blitz"]["last_rating"] == 1505


def test_empty_input_is_safe():
    wld = analytics.win_loss_draw([], "pavan")
    assert wld["games"] == 0 and wld["win_rate"] == 0.0
