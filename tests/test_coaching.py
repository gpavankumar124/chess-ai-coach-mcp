"""Pure-logic tests for coaching.py using synthetic game reviews."""

import coaching
import review as review_mod

_LABELS = ("brilliant", "great", "best", "good", "book",
           "inaccuracy", "mistake", "blunder", "forced", "unknown")


def make_review(link, result, opening, moves):
    """moves: list of {color, move_number, phase, cp, cpl, classification}."""
    rows = []
    for i, m in enumerate(moves, start=1):
        rows.append({
            "ply": i, "move_number": m["move_number"], "color": m["color"],
            "san": m.get("san", "Nf3"), "played_uci": "g1f3", "phase": m["phase"],
            "eval_before": "+0.10", "eval_after": "+0.10",
            "cp": m["cp"], "cpl": m["cpl"], "classification": m["classification"],
        })

    def summ(color):
        mine = [r for r in rows if r["color"] == color]
        counts = dict.fromkeys(_LABELS, 0)
        for r in mine:
            counts[r["classification"]] += 1
        cpls = [r["cpl"] for r in mine if r["cpl"] is not None]
        acpl = round(sum(cpls) / len(cpls)) if cpls else None
        acc = max(20.0, 95.0 - (acpl or 0) / 4)
        return {"accuracy": round(acc, 1), "acpl": acpl,
                "moves_scored": len(cpls), "counts": counts}

    return {
        "schema": review_mod.SCHEMA_VERSION,
        "game": {"white": "student", "black": "opp", "result": result,
                 "eco": "C50", "opening": opening, "termination": None, "link": link,
                 "total_plies": len(rows), "analyzed_plies": len(rows), "truncated": False},
        "players": {"white": summ("white"), "black": summ("black")},
        "moves": rows, "key_moments": [], "biggest_swings": [],
    }


def _clean(color, mn, phase):
    return {"color": color, "move_number": mn, "phase": phase,
            "cp": 20, "cpl": 15, "classification": "best"}


def _endgame_leak(color, mn, cpl):
    return {"color": color, "move_number": mn, "phase": "endgame", "cp": -50, "cpl": cpl,
            "classification": "blunder" if cpl >= 300 else "mistake", "san": "Kf1"}


def endgame_weak_player(n_games):
    """`student` is always White, clean early, leaks badly in the endgame."""
    pairs = []
    for gi in range(n_games):
        moves = []
        for mn in range(1, 9):
            moves += [_clean("white", mn, "opening"), _clean("black", mn, "opening")]
        for mn in range(9, 20):
            moves += [_clean("white", mn, "middlegame"), _clean("black", mn, "middlegame")]
        for mn in range(20, 30):
            moves += [_endgame_leak("white", mn, 220 + (mn % 3) * 90), _clean("black", mn, "endgame")]
        pairs.append((make_review(f"g{gi}", "0-1", "Italian Game", moves), "white"))
    return pairs


class TestOutcomeFor:
    def test_white_win(self):
        assert coaching.outcome_for("1-0", "white") == "win"

    def test_black_loss(self):
        assert coaching.outcome_for("1-0", "black") == "loss"

    def test_draw(self):
        assert coaching.outcome_for("1/2-1/2", "black") == "draw"


class TestConfidence:
    def test_scale(self):
        assert coaching._confidence(2) == "insufficient"
        assert coaching._confidence(5) == "tentative"
        assert coaching._confidence(12) == "moderate"
        assert coaching._confidence(25) == "strong"


def test_phase_breakdown_isolates_endgame_weakness():
    pb = coaching.phase_breakdown(endgame_weak_player(12))
    assert pb["endgame"]["avg_acpl"] > pb["opening"]["avg_acpl"] + 100


def test_weaknesses_flags_endgame_with_confidence():
    w = coaching.weaknesses(endgame_weak_player(12))
    assert w["overall_confidence"] == "moderate"
    assert any("endgame" in f["area"] for f in w["findings"])
    finding = next(f for f in w["findings"] if "endgame" in f["area"])
    assert finding["confidence"] in ("moderate", "strong")
    assert "evidence" in finding and "sample" in finding


def test_small_sample_is_insufficient_and_caveated():
    small = endgame_weak_player(2)
    assert coaching.weaknesses(small)["overall_confidence"] == "insufficient"
    report = coaching.build_report(small, "student", {"games_analyzed": 2})
    assert any("snapshot" in c or "2 game" in c for c in report["caveats"])


def test_biggest_mistakes_sorted_desc():
    bm = coaching.biggest_mistakes(endgame_weak_player(8), top=5)
    assert len(bm) <= 5
    assert all(bm[i]["cpl"] >= bm[i + 1]["cpl"] for i in range(len(bm) - 1))
    assert all(m["phase"] == "endgame" for m in bm)


def test_conversion_detects_unconverted_win():
    moves = [_clean("white", 1, "opening"), _clean("black", 1, "opening"),
             {"color": "white", "move_number": 5, "phase": "middlegame",
              "cp": 320, "cpl": 10, "classification": "best"},
             {"color": "black", "move_number": 5, "phase": "middlegame",
              "cp": 300, "cpl": 5, "classification": "best"},
             {"color": "white", "move_number": 6, "phase": "middlegame",
              "cp": -400, "cpl": 720, "classification": "blunder"}]
    cv = coaching.conversion([(make_review("c1", "0-1", "Italian Game", moves), "white")])
    assert cv["winning_positions_reached"] == 1
    assert cv["won_from_winning"] == 0


def test_full_report_shape_and_recommendations():
    report = coaching.build_report(endgame_weak_player(12), "student", {"games_analyzed": 12})
    for key in ("summary", "by_phase", "by_color", "openings", "conversion",
                "blunder_timing", "biggest_mistakes", "strengths", "weaknesses",
                "recommendations", "caveats"):
        assert key in report
    assert len(report["recommendations"]) >= 1
