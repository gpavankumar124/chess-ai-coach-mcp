"""Whole-game analysis: replay a finished game and score every move with Stockfish.

Combines `games.py` (replay) + `engine.py` (evaluation). No network, no MCP, no
cache here. Raises ValueError on a bad PGN, engine.EngineError if Stockfish fails.

HOW A MOVE IS SCORED
--------------------
For each position we ask Stockfish for the top 2 lines. A move's *centipawn loss*
(CPL) is how much the evaluation dropped, measured from the mover's point of view
and clamped so a huge swing does not dominate:

    cpl = clamp( eval_before_moverPOV - eval_after_moverPOV , 0 , 2000 )

Classification from CPL:

    blunder      cpl >= 300
    mistake      150 <= cpl < 300
    inaccuracy    50 <= cpl < 150
    good move     cpl < 50   ->  brilliant / great / best / book / good

    'great'     : the played move is (near) best AND the 2nd-best move was much
                  worse (>= GREAT_GAP cp) AND the game was still in the balance.
    'brilliant' : a 'great' move that also gives up >= 2 points of material which
                  the engine still rates as best.
    'forced'    : only one legal move - not a real decision, never a mistake.

Accuracy% per player is computed separately from win-probability loss
(the Lichess formula).

These are HEURISTICS. 'brilliant' / 'great' especially will sometimes disagree
with Chess.com's own move labels.
"""

from __future__ import annotations

import math

import chess

import engine
import games
from log import get_logger

_log = get_logger("review")

# --- tuning knobs ----------------------------------------------------------- #
CP_CAP = 1000          # evaluations clamped to +/- 10 pawns for move scoring
BLUNDER_CPL = 300
MISTAKE_CPL = 150
INACCURACY_CPL = 50
BEST_CPL = 20          # this close to the engine line still counts as "best"
GREAT_GAP = 150        # 2nd-best move must be at least this much worse
GREAT_MAX_EDGE = 600   # 'great'/'brilliant' only when not already winning big
SAC_MATERIAL = 2       # points of material given up to count as a sacrifice
BOOK_PLY = 8           # accurate moves this early are "book"

_PIECE_VALUES = {
    chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
    chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0,
}
_PHASE_PIECES = (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)

# Bump when the shape of a move entry / result changes, so old cached reviews
# (keyed with the old version in main.py) are not reused.
SCHEMA_VERSION = 2

_LABELS = [
    "brilliant", "great", "best", "good", "book",
    "inaccuracy", "mistake", "blunder", "forced", "unknown",
]


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(x, hi))


def _phase(board: chess.Board, move_number: int) -> str:
    """Rough game phase: opening (first 10 moves), then by remaining heavy material."""
    if move_number <= 10:
        return "opening"
    points = sum(
        _PIECE_VALUES[pt] * len(board.pieces(pt, color))
        for pt in _PHASE_PIECES
        for color in (chess.WHITE, chess.BLACK)
    )
    return "endgame" if points <= 20 else "middlegame"


def _material_pov(board: chess.Board, mover_is_white: bool) -> int:
    white = sum(_PIECE_VALUES[p] * len(board.pieces(p, chess.WHITE)) for p in chess.PIECE_TYPES)
    black = sum(_PIECE_VALUES[p] * len(board.pieces(p, chess.BLACK)) for p in chess.PIECE_TYPES)
    balance = white - black
    return balance if mover_is_white else -balance


def _white_cp(analysis: dict, line_index: int = 0) -> float | None:
    """Evaluation of a position in centipawns, White's point of view, clamped.

    Mate is mapped to +/- CP_CAP so it always beats a normal evaluation.
    """
    if analysis.get("game_over"):
        return {"1-0": float(CP_CAP), "0-1": float(-CP_CAP)}.get(analysis.get("outcome"), 0.0)

    if line_index == 0:
        ev = analysis.get("eval")
    else:
        lines = analysis.get("lines") or []
        ev = lines[line_index]["eval"] if len(lines) > line_index else None

    if not ev:
        return None
    if ev.get("mate") is not None:
        return float(CP_CAP if ev["mate"] > 0 else -CP_CAP)
    cp = ev.get("cp")
    return None if cp is None else _clamp(float(cp), -CP_CAP, CP_CAP)


def _disp(analysis: dict) -> str:
    """Short human string for a position eval, always White's point of view."""
    if analysis.get("game_over"):
        return analysis.get("outcome") or "="
    ev = analysis.get("eval")
    return ev["string"] if ev else "?"


def _win_percent(cp_moverpov: float) -> float:
    """Chance (0-100) that the side to move wins, from a centipawn eval."""
    cp = _clamp(cp_moverpov, -CP_CAP, CP_CAP)
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def _move_accuracy(win_pct_lost: float) -> float:
    acc = 103.1668 * math.exp(-0.04354 * max(0.0, win_pct_lost)) - 3.1669
    return _clamp(acc, 0.0, 100.0)


def _sacrificed(fen_before: str, uci: str, mover_is_white: bool) -> bool:
    """True if, right after this move, the opponent can win >= SAC_MATERIAL
    points of material with a capture (or the move itself already gave it up)."""
    board = chess.Board(fen_before)
    move = chess.Move.from_uci(uci)
    material_before = _material_pov(board, mover_is_white)
    board.push(move)
    if _material_pov(board, mover_is_white) <= material_before - SAC_MATERIAL:
        return True
    for reply in board.legal_moves:
        if board.is_capture(reply):
            after = board.copy(stack=False)
            after.push(reply)
            if _material_pov(after, mover_is_white) <= material_before - SAC_MATERIAL:
                return True
    return False


def _classify(
    *, cpl: float | None, played_best: bool, forced: bool,
    gap_pov: float | None, cp_before_pov: float, cp_after_pov: float,
    sacrificed: bool, ply: int,
) -> str:
    if cpl is None:
        return "unknown"
    if forced:
        return "forced"
    if cpl >= BLUNDER_CPL:
        return "blunder"
    if cpl >= MISTAKE_CPL:
        return "mistake"
    if cpl >= INACCURACY_CPL:
        return "inaccuracy"

    near_best = played_best or cpl <= BEST_CPL
    critical = gap_pov is not None and gap_pov >= GREAT_GAP and abs(cp_before_pov) <= GREAT_MAX_EDGE
    quiet = abs(cp_before_pov) < 200 and abs(cp_after_pov) < 200

    if near_best and critical and sacrificed and cp_after_pov > -100:
        return "brilliant"
    if near_best and critical:
        return "great"
    if near_best:
        # "book" only for genuinely calm early moves, not an early mate/crush
        return "book" if (ply <= BOOK_PLY and quiet) else "best"
    return "good"


def _comment(entry: dict) -> str:
    c = entry["classification"]
    if c in ("blunder", "mistake", "inaccuracy"):
        txt = f"{c.capitalize()}: {entry['san']} ({entry['eval_before']} -> {entry['eval_after']})."
        if entry.get("best_move"):
            txt += f" Better was {entry['best_move']}."
        return txt
    if c == "great":
        return f"Great move: {entry['san']} was the only move to hold the advantage."
    if c == "brilliant":
        return f"Brilliant: {entry['san']} sacrifices material and still wins."
    return ""


def review_game(pgn: str, movetime: float = 0.15, max_plies: int = 120) -> dict:
    """Analyse an entire finished game. See module docstring for the scoring rules."""
    movetime = float(_clamp(movetime, 0.05, 1.0))
    max_plies = int(_clamp(max_plies, 2, 300))

    replay = games.replay(pgn, include_fens=True, max_moves=max_plies)
    moves = replay["moves"]
    if not moves:
        raise ValueError("This PGN has no moves to analyse.")

    full_game = games.parse_game(pgn)
    total_plies = sum(1 for _ in full_game.mainline_moves())

    # One engine call per position: before every move, plus the final position.
    fens = [m["fen_before"] for m in moves] + [moves[-1]["fen_after"]]
    _log.info("reviewing game: %d positions, movetime=%.2fs (~%.0fs)",
              len(fens), movetime, len(fens) * movetime)
    analyses = [engine.analyze_fen(f, movetime=movetime, multipv=2) for f in fens]

    annotated: list[dict] = []
    per_color = {
        "white": {"accuracies": [], "cpls": [], "counts": dict.fromkeys(_LABELS, 0)},
        "black": {"accuracies": [], "cpls": [], "counts": dict.fromkeys(_LABELS, 0)},
    }

    for i, mv in enumerate(moves):
        color = mv["color"]
        mover_is_white = color == "white"
        pov = 1.0 if mover_is_white else -1.0
        before, after = analyses[i], analyses[i + 1]

        cp_before_w = _white_cp(before)
        cp_after_w = _white_cp(after)
        line1_w = _white_cp(before, line_index=1)
        forced = len(before.get("lines", []) or []) < 2 and not before.get("game_over")

        if cp_before_w is None or cp_after_w is None:
            cpl = None
            gap_pov = None
            cp_before_pov = cp_after_pov = 0.0
        else:
            cpl = _clamp((cp_before_w - cp_after_w) * pov, 0.0, 2 * CP_CAP)
            cp_before_pov = cp_before_w * pov
            cp_after_pov = cp_after_w * pov
            gap_pov = None if line1_w is None else (cp_before_w - line1_w) * pov

        best = before.get("best_move") or {}
        played_best = best.get("uci") == mv["uci"]

        # only bother checking for a sacrifice on moves that are still "good"
        sacrificed = False
        if cpl is not None and cpl < INACCURACY_CPL:
            try:
                sacrificed = _sacrificed(mv["fen_before"], mv["uci"], mover_is_white)
            except (ValueError, AssertionError):
                sacrificed = False

        label = _classify(
            cpl=cpl, played_best=played_best, forced=forced,
            gap_pov=gap_pov, cp_before_pov=cp_before_pov, cp_after_pov=cp_after_pov,
            sacrificed=sacrificed, ply=mv["ply"],
        )

        entry = {
            "ply": mv["ply"],
            "move_number": mv["move_number"],
            "color": color,
            "san": mv["san"],
            "played_uci": mv["uci"],
            "phase": _phase(chess.Board(mv["fen_before"]), mv["move_number"]),
            "eval_before": _disp(before),
            "eval_after": _disp(after),
            "cp": None if cp_after_w is None else round(cp_after_w),
            "cpl": None if cpl is None else round(cpl),
            "classification": label,
        }
        if not played_best and best.get("san"):
            entry["best_move"] = best["san"]
        annotated.append(entry)

        bucket = per_color[color]
        bucket["counts"][label] += 1
        if cpl is not None:
            bucket["cpls"].append(cpl)
            wp_before = _win_percent(cp_before_pov)
            wp_after = _win_percent(cp_after_pov)
            bucket["accuracies"].append(_move_accuracy(wp_before - wp_after))

    def _summ(bucket: dict) -> dict:
        accs = bucket["accuracies"]
        cpls = bucket["cpls"]
        return {
            "accuracy": round(sum(accs) / len(accs), 1) if accs else None,
            "acpl": round(sum(cpls) / len(cpls)) if cpls else None,
            "moves_scored": len(cpls),
            "counts": bucket["counts"],
        }

    key_moments = []
    for e in annotated:
        if e["classification"] in ("blunder", "mistake", "great", "brilliant"):
            km = dict(e)
            km["comment"] = _comment(e)
            key_moments.append(km)

    biggest_swings = sorted(
        (e for e in annotated if e["cpl"] is not None and e["classification"] in ("blunder", "mistake", "inaccuracy")),
        key=lambda e: e["cpl"], reverse=True,
    )[:3]

    headers = replay["headers"]
    return {
        "schema": SCHEMA_VERSION,
        "game": {
            "white": replay["white"],
            "black": replay["black"],
            "result": replay["result"],
            "eco": replay["eco"],
            "opening": replay["opening"],
            "termination": replay["termination"],
            "link": headers.get("Link") or headers.get("Site"),
            "total_plies": total_plies,
            "analyzed_plies": len(moves),
            "truncated": total_plies > len(moves),
        },
        "settings": {"movetime_s": movetime, "max_plies": max_plies},
        "players": {"white": _summ(per_color["white"]), "black": _summ(per_color["black"])},
        "key_moments": key_moments,
        "biggest_swings": [{**e, "comment": _comment(e)} for e in biggest_swings],
        "moves": annotated,
    }
