# Chess AI Coach — MCP Server

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant (such as
**Claude Desktop**) pull a player's games from **Chess.com**, analyze them with
**Stockfish**, and act as a chess coach — spotting mistakes, finding recurring
weaknesses, and writing a coaching report grounded in the player's own games.

Built with Python, [uv](https://docs.astral.sh/uv/), [FastMCP](https://gofastmcp.com),
[python-chess](https://python-chess.readthedocs.io) and Stockfish.

> **Fair-play note:** Stockfish is used only for **finished games and offline
> positions**. This project has no feature that helps during a live game.

---

## What it can do

| Area | Tools |
|---|---|
| **Player data** | `get_player_profile`, `get_player_stats`, `get_game_archives`, `get_games`, `get_recent_games` |
| **Analytics** | `calculate_win_rate`, `analyze_color_performance`, `analyze_openings`, `get_rating_progress` |
| **Game handling** | `get_game_pgn`, `get_recent_game_pgn`, `replay_game`, `describe_position`, `position_after_ply` |
| **Engine** | `engine_status`, `analyze_position`, `best_move` |
| **Game review** | `analyze_game`, `review_recent_game` — every move labelled *brilliant / great / best / good / book / inaccuracy / mistake / blunder / forced*, with per-player accuracy |
| **Coaching** | `find_biggest_mistakes`, `analyze_player_weaknesses`, `generate_coach_report` — patterns across many games, every finding graded by sample-size confidence |
| **Utility** | `ping`, `server_info`, `cache_info`, `clear_cache` |

### Example prompts (in Claude Desktop)

- *"What are magnuscarlsen's blitz and rapid ratings?"*
- *"Over hikaru's last 2 months, does he score better as White or Black?"*
- *"Review my most recent game and tell me where I went wrong."* (use your username)
- *"Generate a coaching report for me from my last 8 games."*
- *"What are my biggest mistakes recently, and which opening am I weakest in?"*

---

## Requirements

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** (`pip install uv` or see their install docs)
- **Stockfish** (the chess engine) — download below
- **Claude Desktop** (or any MCP client)

---

## Setup

### 1. Get the code and dependencies

```powershell
git clone https://github.com/gpavankumar124/chess-ai-coach-mcp.git
cd chess-ai-coach-mcp
uv sync
```

### 2. Install Stockfish

Download the engine from <https://stockfishchess.org/download/> (the "x86-64"
universal build works on any modern CPU), unzip it, and put the executable here:

```
chess-ai-coach-mcp/engine/stockfish-windows-x86-64-universal.exe
```

Any file whose name starts with `stockfish` in the `engine/` folder is picked up
automatically. Alternatively, put Stockfish on your `PATH` or set
`STOCKFISH_PATH` (see `.env.example`).

Check it works:

```powershell
uv run python -c "import engine; print(engine.engine_available())"
```

### 3. Run the server

```powershell
uv run python main.py
```

It speaks MCP over **stdio**, which is how Claude Desktop launches it.

---

## Connect to Claude Desktop

Add this to `claude_desktop_config.json`:

- **Windows (standard install):** `%APPDATA%\Claude\claude_desktop_config.json`
- **Windows (Microsoft Store install):**
  `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude_desktop_config.json`
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "chess-ai-coach": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\path\\to\\chess-ai-coach-mcp",
        "python",
        "main.py"
      ]
    }
  }
}
```

Fully quit Claude Desktop (system tray → **Quit**) and reopen it. The
`chess-ai-coach` tools should appear in the tools menu.

MCP server logs are written next to the config file under `logs\`, and also to
`data/chess_coach.log` in the project.

---

## Architecture

```
Claude Desktop  (MCP client)
      │  stdio
      ▼
main.py  ── @tool() wrappers: validate → fetch → compute → package, always
      │      returning data or {"error": "..."}; every call is logged
      │
      ├── chesscom.py ── Chess.com Published-Data API  ──►  cache.py (SQLite)
      │
      ├── analytics.py ── win/loss, colour, openings, rating progress
      │                    (pure functions over raw game dicts)
      │
      ├── games.py ─────── python-chess: parse PGN, replay moves (SAN + UCI),
      │                    describe a FEN position
      │
      ├── engine.py ────── Stockfish via chess.engine: evaluate a FEN
      │                    (centipawns, always White's perspective), best move
      │
      ├── review.py ────── whole-game analysis: games.replay + engine per
      │                    position → per-move centipawn loss → classification
      │                    → per-player accuracy   (cached)
      │
      └── coaching.py ──── aggregate many reviews → biggest mistakes, phase /
                           colour / opening breakdowns, weaknesses with a
                           sample-size confidence, full report
```

**Layer rules**

- `chesscom` / `analytics` / `games` / `engine` / `review` / `coaching` are pure
  library code — no MCP, no `print()`. `main.py` is the only MCP file.
- Every tool returns its result, or `{"error": "<readable message>"}` — tools
  never raise to the client.
- Inputs are validated before any network call.
- Chess.com stores results per side; the player's colour is always resolved
  before a result is interpreted.
- Engine evaluations are always from **White's** point of view; a review flips to
  the mover's point of view in exactly one place.
- Coaching never states a firm conclusion from a small sample — findings carry
  `insufficient` / `tentative` / `moderate` / `strong` confidence, and reports
  carry explicit caveats.

### Caching

`data/cache.db` (SQLite) stores Chess.com responses and game reviews so repeated
requests don't re-hit the network or re-run the engine. Finished months and game
reviews are cached permanently (keyed by content + parameters); profiles and
stats expire after a few hours. A cache failure never breaks a tool — it just
falls through to the live source.

---

## Development

```powershell
uv run pytest                       # unit tests (engine tests auto-skip if no Stockfish)
uv run pytest -m "not network"      # skip the tests that hit Chess.com
uv run pytest -m network            # only the Chess.com API tests
uv run python -u scripts/smoke_test.py   # exercise every MCP tool through an in-memory client
uv run fastmcp inspect main.py      # list the registered tools
```

Set `CHESS_COACH_LOG_LEVEL=DEBUG` for verbose logs.

---

## Limitations

- Move labels (`brilliant`, `great`, …) are **heuristics** based on centipawn
  loss, a second-line gap, and a simple material-sacrifice check. They will
  sometimes disagree with Chess.com's own labels.
- Analysis quality depends on the engine time per move (`movetime`). The defaults
  favour speed; raise `movetime` for sharper positions.
- Coaching insight is only as good as the sample — a report from 5 games is a
  snapshot, not a verdict, and says so.

---

## License

[MIT](LICENSE) © 2026 Gajji Sai Pavan Kumar

Stockfish is distributed separately under the GPLv3 and is **not** included in
this repository.
