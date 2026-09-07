"""Turn many game reviews into coaching insight.

Input everywhere is `pairs`: a list of ``(review, side)`` tuples, where
``review`` is the dict returned by ``review.review_game(...)`` and ``side`` is
``"white"`` or ``"black"`` - the colour the coached player had in that game.

Guiding rule (from the project brief): never state a firm conclusion from a
small sample. Every weakness finding carries a ``confidence`` derived from how
many games it is based on:

    < 3 games   -> "insufficient"
    3-7 games   -> "tentative"
    8-19 games  -> "moderate"
    >= 20 games -> "strong"

Pure functions - no network, no engine, no MCP.
"""

from __future__ import annotations

CONF_STRONG = 20
CONF_MODERATE = 8
CONF_TENTATIVE = 3

_PHASES = ("opening", "middlegame", "endgame")
_OUTCOME_KEY = {"win": "wins", "loss": "losses", "draw": "draws"}


def _avg(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _confidence(n_games: int) -> str:
    if n_games >= CONF_STRONG:
        return "strong"
    if n_games >= CONF_MODERATE:
        return "moderate"
    if n_games >= CONF_TENTATIVE:
        return "tentative"
    return "insufficient"


def outcome_for(result: str, side: str) -> str:
    """'win' / 'loss' / 'draw' for the coached player, from the PGN result string."""
    if result == "1/2-1/2":
        return "draw"
    if result in ("1-0", "0-1"):
        white_won = result == "1-0"
        return "win" if white_won == (side == "white") else "loss"
    return "unknown"


def player_moves(review: dict, side: str) -> list[dict]:
    return [m for m in review.get("moves", []) if m.get("color") == side]


def _summary(review: dict, side: str) -> dict:
    return (review.get("players", {}) or {}).get(side, {}) or {}


def _opening_family(review: dict) -> str:
    name = review.get("game", {}).get("opening") or "Unknown"
    return " ".join(name.split()[:2]) or "Unknown"


# --------------------------------------------------------------------------- #
# raw signals                                                                  #
# --------------------------------------------------------------------------- #


def biggest_mistakes(pairs, top: int = 10, include_inaccuracies: bool = False) -> list[dict]:
    wanted = {"blunder", "mistake"}
    if include_inaccuracies:
        wanted.add("inaccuracy")

    out = []
    for review, side in pairs:
        game = review.get("game", {})
        dots = "." if side == "white" else "..."
        for m in player_moves(review, side):
            if m.get("classification") in wanted and m.get("cpl") is not None:
                out.append({
                    "game": game.get("link"),
                    "opening": _opening_family(review),
                    "result": outcome_for(game.get("result", ""), side),
                    "move": f'{m["move_number"]}{dots} {m["san"]}',
                    "classification": m["classification"],
                    "cpl": m["cpl"],
                    "phase": m.get("phase"),
                    "swing": f'{m["eval_before"]} -> {m["eval_after"]}',
                    "better_move": m.get("best_move"),
                })
    out.sort(key=lambda x: x["cpl"], reverse=True)
    return out[:top]


def phase_breakdown(pairs) -> dict:
    acc = {p: {"moves": 0, "cpl_sum": 0, "blunders": 0, "mistakes": 0, "inaccuracies": 0}
           for p in _PHASES}
    for review, side in pairs:
        for m in player_moves(review, side):
            p = m.get("phase")
            if p not in acc or m.get("cpl") is None:
                continue
            a = acc[p]
            a["moves"] += 1
            a["cpl_sum"] += m["cpl"]
            cls = m.get("classification")
            if cls == "blunder":
                a["blunders"] += 1
            elif cls == "mistake":
                a["mistakes"] += 1
            elif cls == "inaccuracy":
                a["inaccuracies"] += 1

    out = {}
    for p, a in acc.items():
        n = a["moves"]
        out[p] = {
            "moves": n,
            "avg_acpl": round(a["cpl_sum"] / n) if n else None,
            "blunders": a["blunders"],
            "mistakes": a["mistakes"],
            "inaccuracies": a["inaccuracies"],
            "error_rate_per_100": round((a["blunders"] + a["mistakes"]) / n * 100, 1) if n else None,
        }
    return out


def color_breakdown(pairs) -> dict:
    by_color: dict[str, list[dict]] = {"white": [], "black": []}
    for review, side in pairs:
        by_color[side].append(_summary(review, side))

    out = {}
    for color, summaries in by_color.items():
        accs = [s["accuracy"] for s in summaries if s.get("accuracy") is not None]
        acpls = [s["acpl"] for s in summaries if s.get("acpl") is not None]
        blunders = sum((s.get("counts") or {}).get("blunder", 0) for s in summaries)
        out[color] = {
            "games": len(summaries),
            "avg_accuracy": round(_avg(accs), 1) if accs else None,
            "avg_acpl": round(_avg(acpls)) if acpls else None,
            "blunders": blunders,
            "blunders_per_game": round(blunders / len(summaries), 2) if summaries else None,
        }
    return out


def opening_breakdown(pairs, min_games: int = 2) -> list[dict]:
    groups: dict[str, dict] = {}
    for review, side in pairs:
        fam = _opening_family(review)
        grp = groups.setdefault(fam, {"games": 0, "wins": 0, "losses": 0, "draws": 0, "acc": []})
        grp["games"] += 1
        oc = outcome_for(review.get("game", {}).get("result", ""), side)
        if oc in _OUTCOME_KEY:
            grp[_OUTCOME_KEY[oc]] += 1
        s = _summary(review, side)
        if s.get("accuracy") is not None:
            grp["acc"].append(s["accuracy"])

    out = []
    for fam, g in groups.items():
        if g["games"] < min_games or fam == "Unknown":
            continue
        n = g["games"]
        out.append({
            "opening": fam,
            "games": n,
            "wins": g["wins"], "losses": g["losses"], "draws": g["draws"],
            "score_pct": round((g["wins"] + 0.5 * g["draws"]) / n * 100, 1),
            "avg_accuracy": round(_avg(g["acc"]), 1) if g["acc"] else None,
        })
    out.sort(key=lambda x: x["games"], reverse=True)
    return out


def blunder_timing(pairs) -> dict:
    buckets = {"moves_1_15": 0, "moves_16_30": 0, "moves_31_plus": 0}
    total = 0
    for review, side in pairs:
        for m in player_moves(review, side):
            if m.get("classification") == "blunder":
                total += 1
                mn = m.get("move_number", 0)
                if mn <= 15:
                    buckets["moves_1_15"] += 1
                elif mn <= 30:
                    buckets["moves_16_30"] += 1
                else:
                    buckets["moves_31_plus"] += 1
    return {"total_blunders": total, "by_move_range": buckets}


def conversion(pairs) -> dict:
    """How well the player converts winning positions / saves losing ones.

    A position counts as "winning" for the player at +2.0 (from their point of
    view) at any point in the game, and "losing" at -2.0.
    """
    reached_win = won_from_win = reached_loss = held_loss = 0
    for review, side in pairs:
        game = review.get("game", {})
        oc = outcome_for(game.get("result", ""), side)
        pov = 1 if side == "white" else -1
        evals = [m["cp"] * pov for m in review.get("moves", []) if m.get("cp") is not None]
        if not evals:
            continue
        if max(evals) >= 200:
            reached_win += 1
            if oc == "win":
                won_from_win += 1
        if min(evals) <= -200:
            reached_loss += 1
            if oc != "loss":
                held_loss += 1
    return {
        "winning_positions_reached": reached_win,
        "won_from_winning": won_from_win,
        "conversion_rate": round(won_from_win / reached_win, 2) if reached_win else None,
        "losing_positions_reached": reached_loss,
        "held_or_saved": held_loss,
    }


# --------------------------------------------------------------------------- #
# interpretation: weaknesses, strengths, recommendations                       #
# --------------------------------------------------------------------------- #


def _totals(pairs) -> dict:
    blunders = mistakes = moves = 0
    for review, side in pairs:
        s = _summary(review, side)
        counts = s.get("counts") or {}
        blunders += counts.get("blunder", 0)
        mistakes += counts.get("mistake", 0)
        moves += s.get("moves_scored", 0)
    return {"blunders": blunders, "mistakes": mistakes, "moves": moves}


def weaknesses(pairs) -> dict:
    n = len(pairs)
    findings: list[dict] = []

    pb = phase_breakdown(pairs)
    scored = [(p, d) for p, d in pb.items() if d["moves"] >= 12 and d["avg_acpl"] is not None]
    if len(scored) >= 2:
        scored.sort(key=lambda kv: kv[1]["avg_acpl"], reverse=True)
        worst, wd = scored[0]
        best, bd = scored[-1]
        if wd["avg_acpl"] >= bd["avg_acpl"] + 25 and wd["avg_acpl"] >= 55:
            findings.append({
                "area": f"{worst} play",
                "finding": (f"Your {worst} is weaker than your {best}: "
                            f"average centipawn loss {wd['avg_acpl']} vs {bd['avg_acpl']}."),
                "evidence": {worst: wd, best: bd},
                "sample": {"games": n, "moves_in_phase": wd["moves"]},
                "confidence": _confidence(n),
            })

    cb = color_breakdown(pairs)
    w, b = cb["white"], cb["black"]
    if w["games"] >= 3 and b["games"] >= 3 and w["avg_acpl"] and b["avg_acpl"]:
        if abs(w["avg_acpl"] - b["avg_acpl"]) >= 20:
            worse = "White" if w["avg_acpl"] > b["avg_acpl"] else "Black"
            findings.append({
                "area": f"playing as {worse}",
                "finding": (f"You play noticeably worse as {worse} "
                            f"(avg centipawn loss white {w['avg_acpl']} / black {b['avg_acpl']})."),
                "evidence": cb,
                "sample": {"white_games": w["games"], "black_games": b["games"]},
                "confidence": _confidence(min(w["games"], b["games"])),
            })

    t = _totals(pairs)
    if t["moves"] >= 40:
        per100 = t["blunders"] / t["moves"] * 100
        if per100 >= 3:
            findings.append({
                "area": "blunders",
                "finding": (f"You blunder about {per100:.1f} times per 100 moves "
                            f"({t['blunders']} blunders in {n} games)."),
                "evidence": {"blunders": t["blunders"], "moves_analyzed": t["moves"]},
                "sample": {"games": n, "moves": t["moves"]},
                "confidence": _confidence(n),
            })

    bt = blunder_timing(pairs)
    if bt["total_blunders"] >= 4:
        late = bt["by_move_range"]["moves_31_plus"]
        if late / bt["total_blunders"] >= 0.5:
            findings.append({
                "area": "late-game / time management",
                "finding": (f"{late} of your {bt['total_blunders']} blunders happen after move 30 - "
                            f"possible time pressure or endgame-technique issue."),
                "evidence": bt,
                "sample": {"games": n, "blunders": bt["total_blunders"]},
                "confidence": _confidence(n),
            })

    cv = conversion(pairs)
    if cv["winning_positions_reached"] >= 4 and cv["conversion_rate"] is not None and cv["conversion_rate"] < 0.6:
        findings.append({
            "area": "converting winning positions",
            "finding": (f"You reached a clearly winning position in "
                        f"{cv['winning_positions_reached']} games but won only "
                        f"{cv['won_from_winning']}."),
            "evidence": cv,
            "sample": {"winning_positions": cv["winning_positions_reached"]},
            "confidence": _confidence(cv["winning_positions_reached"]),
        })

    for o in opening_breakdown(pairs, min_games=3):
        if o["score_pct"] <= 35:
            findings.append({
                "area": f"the {o['opening']}",
                "finding": (f"You score just {o['score_pct']}% in the {o['opening']} "
                            f"across {o['games']} games."),
                "evidence": o,
                "sample": {"games": o["games"]},
                "confidence": _confidence(o["games"]),
            })

    return {"based_on_games": n, "overall_confidence": _confidence(n), "findings": findings}


def strengths(pairs) -> list[str]:
    out = []
    n = len(pairs)
    pb = phase_breakdown(pairs)
    if pb["opening"]["moves"] >= 15 and (pb["opening"]["avg_acpl"] or 999) <= 35:
        out.append(f"Solid opening play (avg centipawn loss {pb['opening']['avg_acpl']}).")

    t = _totals(pairs)
    if t["moves"] >= 40:
        per100 = t["blunders"] / t["moves"] * 100
        if per100 <= 1.5:
            out.append(f"Low blunder rate ({per100:.1f} per 100 moves).")

    accs = [_summary(r, s).get("accuracy") for r, s in pairs]
    accs = [a for a in accs if a is not None]
    if accs and _avg(accs) >= 85:
        out.append(f"High overall accuracy ({round(_avg(accs), 1)}% across {n} games).")

    cv = conversion(pairs)
    if cv["winning_positions_reached"] >= 4 and (cv["conversion_rate"] or 0) >= 0.8:
        out.append(f"Converts winning positions well ({cv['won_from_winning']}/{cv['winning_positions_reached']}).")

    return out


_ADVICE = {
    "opening play": "Review your first ~12 moves in these openings with the engine - you are losing ground before the middlegame even starts.",
    "middlegame play": "Slow down on the first critical decision each game; do a full candidate-move check before committing.",
    "endgame play": "Drill fundamental endgames (king-and-pawn, rook endgames). Your endgame centipawn loss is well above your other phases.",
    "converting winning positions": "When you are up material or +2, simplify: trade pieces, avoid sharp complications, don't rush.",
    "late-game / time management": "Budget your clock so you still have time for moves 25-40 - that is where your blunders cluster.",
    "blunders": "Before every move, spend two seconds: is any piece of mine hanging? Does my move drop a tactic?",
}


def recommendations(findings: list[dict]) -> list[str]:
    recs = []
    for f in findings:
        area = f["area"]
        if area in _ADVICE:
            recs.append(_ADVICE[area])
        elif area.startswith("playing as "):
            recs.append(f"Prepare your {area.split()[-1]} repertoire - your results and accuracy there lag your other colour.")
        elif area.startswith("the "):
            recs.append(f"Pick a clear line in {area[4:]} and study it; you are scoring poorly there.")
    # de-duplicate, keep order
    seen = set()
    return [r for r in recs if not (r in seen or seen.add(r))]


def _caveats(n_games: int, meta: dict) -> list[str]:
    out = []
    if n_games < CONF_TENTATIVE:
        out.append(f"Only {n_games} game(s) analyzed - this is a snapshot, not a reliable pattern.")
    elif n_games < CONF_MODERATE:
        out.append(f"{n_games} games analyzed - findings are tentative; analyze more games to confirm them.")
    if meta.get("truncated_games"):
        out.append(f"{meta['truncated_games']} game(s) were only partly analyzed (move cap) - "
                   f"endgame stats for those are incomplete.")
    out.append("Engine analysis at low movetime can misjudge sharp positions; "
               "treat individual move labels as indicative, not final.")
    return out


def build_report(pairs, username: str, meta: dict) -> dict:
    n = len(pairs)
    record = {"win": 0, "loss": 0, "draw": 0}
    accs, acpls = [], []
    for review, side in pairs:
        oc = outcome_for(review.get("game", {}).get("result", ""), side)
        if oc in record:
            record[oc] += 1
        s = _summary(review, side)
        if s.get("accuracy") is not None:
            accs.append(s["accuracy"])
        if s.get("acpl") is not None:
            acpls.append(s["acpl"])

    t = _totals(pairs)
    w = weaknesses(pairs)

    return {
        "player": username,
        "scope": meta,
        "confidence": w["overall_confidence"],
        "summary": {
            "games_analyzed": n,
            "record": record,
            "avg_accuracy": round(_avg(accs), 1) if accs else None,
            "avg_acpl": round(_avg(acpls)) if acpls else None,
            "your_moves_analyzed": t["moves"],
            "total_blunders": t["blunders"],
            "total_mistakes": t["mistakes"],
            "blunders_per_game": round(t["blunders"] / n, 2) if n else None,
        },
        "by_phase": phase_breakdown(pairs),
        "by_color": color_breakdown(pairs),
        "openings": opening_breakdown(pairs, min_games=2),
        "conversion": conversion(pairs),
        "blunder_timing": blunder_timing(pairs),
        "biggest_mistakes": biggest_mistakes(pairs, top=6),
        "strengths": strengths(pairs),
        "weaknesses": w["findings"],
        "recommendations": recommendations(w["findings"]),
        "caveats": _caveats(n, meta),
    }
