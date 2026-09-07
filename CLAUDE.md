# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

**Chess AI Coach MCP Server** — an MCP server (FastMCP) that lets an AI assistant
(Claude Desktop) fetch and analyze **Chess.com** data and act as a chess coach.

Being built **phase by phase** as a learning project. See the roadmap and the
per-phase status below. The owner wants: build → test → then a teaching
explanation → **stop and wait** before the next phase. Do not auto-start the next
phase.

## Commands

```powershell
uv sync                                   # install / update dependencies
uv run python main.py                      # run the MCP server (stdio transport)
uv run fastmcp inspect main.py             # list the server's tools

uv run pytest                              # unit tests (engine tests auto-skip if no Stockfish)
uv run pytest -m "not network"             # skip the tests that hit Chess.com
uv run pytest -m network                   # only the Chess.com API tests
uv run python -u scripts/smoke_test.py     # in-memory MCP client: exercises every tool (slow: engine)
```

The pytest suite is in `tests/` (`conftest.py` at repo root puts the modules on
`sys.path`). Markers: `network` (Chess.com API), `engine` (needs Stockfish,
auto-skipped via the `stockfish` fixture). `temp_cache` fixture points `cache`
at a throwaway DB.

`scripts/smoke_test.py` is the one remaining ad-hoc script - a full MCP
integration demo. Use `python -u`: it makes many engine calls and buffered
output makes a slow run look like a hang. If a run seems stuck, check for
orphaned `stockfish*` processes from a killed earlier run.

## Architecture

Three layers, kept strictly separate:

| Layer | File | Rule |
|-------|------|------|
| **Data** | `chesscom.py` | Talks to the Chess.com API. Pure functions, no MCP. Raises `ChessComError` on any failure. Checks `cache` before the network (toggle: `use_cache=True`). |
| **Cache** | `cache.py` | One SQLite file `data/cache.db`, one `cache(namespace, key, value, fetched_at)` table. `load` / `save` / `stats` / `clear`. All DB errors are swallowed - a cache failure must never break a tool. |
| **Analytics** | `analytics.py` | Pure functions over `list[dict]` of raw Chess.com game objects. No network, no MCP. Fully unit-testable. |
| **Chess** | `games.py` | python-chess wrappers: `parse_game`, `replay` (SAN+UCI, optional FENs), `describe_position(fen)`, `position_after_ply`. Pure; raises `ValueError` on bad PGN/FEN. |
| **Engine** | `engine.py` | Stockfish via `chess.engine`. `find_stockfish()`, one shared `SimpleEngine` process, `analyze_fen(fen, movetime, multipv)`, `close_engine()`. Eval is ALWAYS White's perspective. Raises `EngineError`. Closed via `main._lifespan` + `atexit`. |
| **Review** | `review.py` | Whole-game analysis: `review_game(pgn, movetime, max_plies)` combines `games.replay` + `engine.analyze_fen` (multipv=2 per position). Per-move: `phase` (opening/middlegame/endgame), `cp` (White POV), centipawn loss (capped ±1000, mover POV) → classification (brilliant/great/best/good/book/inaccuracy/mistake/blunder/forced). Per-player accuracy via Lichess win%/CPL formulas. `SCHEMA_VERSION` bumped on output-shape change. Pure; no cache. |
| **Coaching** | `coaching.py` | Aggregates many reviews. Input is `pairs` = list of `(review, side)`. `biggest_mistakes`, `phase_breakdown`, `color_breakdown`, `opening_breakdown`, `blunder_timing`, `conversion`, `weaknesses` (findings + `_confidence(n_games)`: <3 insufficient / 3-7 tentative / 8-19 moderate / ≥20 strong), `strengths`, `recommendations`, `build_report`. Pure. |
| **Logging** | `log.py` | `get_logger(name)` → stderr + rotating `data/chess_coach.log` (stdout is the MCP channel — never `print()`). `@logged` decorator logs a tool call, its duration and any `{"error": ...}`. `CHESS_COACH_LOG_LEVEL` env var. |
| **MCP** | `main.py` | `@tool()` (= `mcp.tool()` + `logged`) wrappers only: validate inputs → fetch via `chesscom` → compute via `analytics` / `games` / `engine` / `review` / `coaching` → package the result. Game reviews cached in namespace `review` (key = `v{SCHEMA_VERSION}` + game Link URL + params, TTL none). `_collect_reviews` fetches + reviews N recent games into `pairs`. |

Cache TTLs live in `chesscom.py`: profile 24h, stats/archives 6h, past months forever, current month 1h.

Data flow:

```
Claude Desktop → @mcp.tool() (main.py) → validation
              → chesscom.py → Chess.com API → raw JSON
              → analytics.py → computed summary
              → back to Claude
```

### Conventions

- **Every tool returns data on success or `{"error": "<readable message>"}` on
  failure.** Tools never raise to the MCP client. Catch `chesscom.ChessComError`
  and return its message.
- **Validate inputs before any network call.** Helpers in `main.py`
  (`_check_username`, `_check_year_month`, `_check_limit`, `_check_months`) return
  an error string or `None`; chain them with `or`.
- **Player perspective matters.** Chess.com stores results per side
  (`game["white"]`, `game["black"]`). Always resolve the player's color first via
  `analytics.player_color` / `analytics.player_side` before interpreting a result.
- `analytics.classify_result`: `"win"` → win; a fixed set (`DRAW_RESULTS`) → draw;
  **everything else → loss**. Do not enumerate loss reasons.
- Exclude non-standard games: `game["rules"] != "chess"` is filtered out of
  analytics.
- Keep tool output small: summarize game lists (`_summarize_game`, no PGN),
  downsample time series (`analytics._downsample`, ~40 points).
- `_collect_recent_months(username, months)` fetches the last N monthly archives
  (one API call each; capped at `_MAX_ANALYSIS_MONTHS = 12`). It returns a
  `list[dict]` or an `{"error": ...}` dict — callers check `isinstance(x, dict)`.
- Chess.com needs a descriptive `User-Agent` header (set in `chesscom.HEADERS`);
  no API key.

## Style

- Simple, readable, beginner-friendly. This is a teaching project — favor clarity
  over cleverness. No premature abstraction.
- Match the existing docstring style: one summary line, then an `Args:` block for
  tools.
- Python 3.11+ syntax is fine (`str | None`, etc.).

## Phase status

- [x] **1** — project setup, FastMCP server, `ping` / `server_info`
- [x] **2** — `chesscom.py` Chess.com API client
- [x] **3** — core tools: `get_player_profile`, `get_player_stats`,
      `get_game_archives`, `get_games`, `get_recent_games`
- [x] **4** — analytics: `calculate_win_rate`, `analyze_color_performance`,
      `analyze_openings`, `get_rating_progress`
- [x] **5** — SQLite cache (`cache.py`) + `cache_info` / `clear_cache` tools
- [x] **6** — python-chess (`games.py`) + `get_game_pgn` / `get_recent_game_pgn` / `replay_game` / `describe_position` / `position_after_ply`
- [x] **7** — Stockfish (`engine.py`, `engine/` folder) + `engine_status` / `analyze_position` / `best_move`
- [x] **8** — whole-game analysis (`review.py`) + `analyze_game` / `review_recent_game`; move labels incl. brilliant/great/mistake/blunder, per-player accuracy
- [x] **9** — coaching (`coaching.py`): `find_biggest_mistakes`, `analyze_player_weaknesses`, `generate_coach_report`; confidence-graded findings, explicit caveats
- [x] **10** — polish: `log.py` logging, `tests/` pytest suite, README, LICENSE (MIT), `.env.example`, pushed to GitHub

## Constraints

- **Stockfish is for completed games / offline positions only.** Never build
  anything that assists a live, ongoing game.
- Do not invent Chess.com API fields or engine results. When unsure, check the
  official published-data API docs.

## Environment

- Stockfish 19 lives at `engine/stockfish-windows-x86-64-universal.exe` (gitignored).
  `chess.engine.SimpleEngine.quit()` deadlocks if called from an atexit handler
  mid-shutdown on Windows - `engine.close_engine()` guards against this with a
  threaded timeout + `close()` fallback.
- Windows 11 + PowerShell. Owner runs the **Microsoft Store** build of Claude
  Desktop; its config is at
  `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`
  (not `%APPDATA%\Claude`). MCP logs sit next to it under `logs\`.
- The `chess-ai-coach` server entry runs
  `uv run --directory "<repo>" python main.py`.
- After changing tools, Claude Desktop must be fully quit from the system tray and
  reopened.
