"""Parsing and replaying chess games with python-chess.

Pure functions - no network, no MCP. Input is a PGN string (as Chess.com
returns it in game["pgn"]) or a FEN string.

Key ideas:
  * A `chess.Board` is a full board state. `board.push(move)` plays a move and
    mutates the board; `board.fen()` serializes the state as FEN text.
  * A move can be written as SAN ("Nf3", human) or UCI ("g1f3", engine).
  * `chess.pgn.read_game` turns PGN text into a Game with headers + a move list.
"""

from __future__ import annotations

import io

import chess
import chess.pgn

# Standard piece values, used only for a rough material-balance number.
_PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}


def parse_game(pgn: str) -> chess.pgn.Game:
    """Parse a PGN string into a chess.pgn.Game. Raises ValueError if it can't."""
    if not pgn or not pgn.strip():
        raise ValueError("PGN is empty.")
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        raise ValueError("Could not parse PGN.")
    # read_game is lenient; if there are hard errors it records them.
    if game.errors:
        raise ValueError(f"PGN parse error: {game.errors[0]}")
    return game


def _material(board: chess.Board) -> dict:
    """Rough material count for each side and the white-minus-black balance."""
    def side_value(color: bool) -> int:
        return sum(
            _PIECE_VALUES[pt] * len(board.pieces(pt, color))
            for pt in chess.PIECE_TYPES
        )

    white = side_value(chess.WHITE)
    black = side_value(chess.BLACK)
    return {"white": white, "black": black, "balance": white - black}


def describe_position(fen: str) -> dict:
    """Describe a single position given by FEN: whose move, legal moves, flags."""
    try:
        board = chess.Board(fen)
    except ValueError as exc:
        raise ValueError(f"Invalid FEN: {exc}")

    legal_san = sorted(board.san(m) for m in board.legal_moves)
    return {
        "fen": board.fen(),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "fullmove_number": board.fullmove_number,
        "halfmove_clock": board.halfmove_clock,
        "castling_rights": board.castling_xfen().split()[-1] if board.castling_rights else "-",
        "num_legal_moves": len(legal_san),
        "legal_moves": legal_san,
        "is_check": board.is_check(),
        "is_checkmate": board.is_checkmate(),
        "is_stalemate": board.is_stalemate(),
        "is_insufficient_material": board.is_insufficient_material(),
        "is_game_over": board.is_game_over(),
        "material": _material(board),
        "ascii": str(board),
    }


def replay(pgn: str, include_fens: bool = False, max_moves: int = 400) -> dict:
    """Replay a game from its PGN, returning headers and every move.

    Each move entry has the move number, side, SAN and UCI. With
    include_fens=True it also has the FEN before and after the move (useful for
    feeding positions to an engine later).
    """
    game = parse_game(pgn)
    headers = dict(game.headers)

    board = game.board()  # respects a [FEN] header if the game is not standard
    moves: list[dict] = []
    for ply, move in enumerate(game.mainline_moves(), start=1):
        if ply > max_moves:
            break
        fen_before = board.fen()
        san = board.san(move)  # must be computed BEFORE pushing the move
        entry = {
            "ply": ply,
            "move_number": board.fullmove_number,
            "color": "white" if board.turn == chess.WHITE else "black",
            "san": san,
            "uci": move.uci(),
        }
        board.push(move)
        if include_fens:
            entry["fen_before"] = fen_before
            entry["fen_after"] = board.fen()
        moves.append(entry)

    return {
        "headers": headers,
        "white": headers.get("White"),
        "black": headers.get("Black"),
        "result": headers.get("Result"),
        "termination": headers.get("Termination"),
        "eco": headers.get("ECO"),
        "opening": headers.get("ECOUrl", "").rstrip("/").split("/")[-1].replace("-", " ") or None,
        "total_plies": len(moves),
        "final_fen": board.fen(),
        "moves": moves,
    }


def position_after_ply(pgn: str, ply: int) -> dict:
    """Replay the game up to `ply` half-moves, then describe the position.

    ply=0 is the starting position; ply=1 is after White's first move, etc.
    """
    if ply < 0:
        raise ValueError("ply must be 0 or greater.")
    game = parse_game(pgn)
    board = game.board()

    played = 0
    last_san = None
    for move in game.mainline_moves():
        if played >= ply:
            break
        last_san = board.san(move)
        board.push(move)
        played += 1

    info = describe_position(board.fen())
    info["plies_played"] = played
    info["last_move_san"] = last_san
    return info
