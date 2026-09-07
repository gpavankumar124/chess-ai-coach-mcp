"""Phase 1 smoke test: start the server in-memory and exercise its tools."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastmcp import Client

from main import mcp


async def run() -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()
        print("Registered tools:")
        for t in tools:
            print(f"  - {t.name}: {t.description.splitlines()[0]}")

        result = await client.call_tool("ping", {"message": "phase 1 works"})
        print("\nping ->", result.data)

        info = await client.call_tool("server_info", {})
        print("server_info ->", info.data)

        prof = await client.call_tool("get_player_profile", {"username": "hikaru"})
        print("\nget_player_profile -> name:", prof.data.get("name"))

        stats = await client.call_tool("get_player_stats", {"username": "hikaru"})
        print("get_player_stats -> blitz rating:",
              stats.data.get("chess_blitz", {}).get("last", {}).get("rating"))

        arch = await client.call_tool("get_game_archives", {"username": "hikaru"})
        print("get_game_archives -> count:", arch.data.get("count"),
              "latest:", arch.data.get("latest"))

        latest = arch.data["latest"]  # "YYYY/MM"
        y, m = (int(x) for x in latest.split("/"))
        mg = await client.call_tool("get_games", {"username": "hikaru", "year": y, "month": m})
        print(f"get_games({latest}) -> count:", mg.data.get("count"))

        recent = await client.call_tool("get_recent_games", {"username": "hikaru", "limit": 5})
        print("get_recent_games(limit=5) -> count:", recent.data.get("count"))
        for g in recent.data.get("games", []):
            print("   ", g["end_time"], g["time_class"],
                  g["white"]["username"], "vs", g["black"]["username"])

        print("\n--- validation / error paths ---")
        for args in (
            {"username": ""},
            {"username": "no_such_user_xyz_123"},
        ):
            r = await client.call_tool("get_player_profile", args)
            print("get_player_profile", args, "->", r.data.get("error"))
        r = await client.call_tool("get_games", {"username": "hikaru", "year": 1990, "month": 5})
        print("get_games bad year ->", r.data.get("error"))
        r = await client.call_tool("get_recent_games", {"username": "hikaru", "limit": 999})
        print("get_recent_games bad limit ->", r.data.get("error"))

        print("\n--- analytics tools (hikaru, 1 month) ---")
        wr = await client.call_tool("calculate_win_rate", {"username": "hikaru", "months": 1})
        print("calculate_win_rate ->", {k: wr.data[k] for k in ("games", "wins", "losses", "draws", "score_pct")})

        cp = await client.call_tool("analyze_color_performance", {"username": "hikaru", "months": 1})
        print("analyze_color_performance -> white score%:", cp.data["white"]["score_pct"],
              "black score%:", cp.data["black"]["score_pct"])

        op = await client.call_tool("analyze_openings", {"username": "hikaru", "months": 1, "min_games": 10})
        print("analyze_openings -> reported:", op.data["openings_reported"],
              "top:", op.data["openings"][0]["opening"] if op.data["openings"] else None)

        rp = await client.call_tool("get_rating_progress", {"username": "hikaru", "months": 1})
        print("get_rating_progress -> classes:", list(rp.data["by_time_class"].keys()))

        r = await client.call_tool("calculate_win_rate", {"username": "hikaru", "months": 99})
        print("win_rate bad months ->", r.data.get("error"))

        print("\n--- cache tools ---")
        ci = await client.call_tool("cache_info", {})
        print("cache_info -> entries:", ci.data["total_entries"],
              "namespaces:", {k: v["entries"] for k, v in ci.data["namespaces"].items()})
        cc = await client.call_tool("clear_cache", {"namespace": "profile"})
        print("clear_cache(profile) ->", cc.data)
        bad = await client.call_tool("clear_cache", {"namespace": "nonsense"})
        print("clear_cache(bad) ->", bad.data.get("error"))

        print("\n--- python-chess tools ---")
        gp = await client.call_tool("get_recent_game_pgn", {"username": "hikaru", "index": 0})
        pgn = gp.data["pgn"]
        print("get_recent_game_pgn -> pgn length:", len(pgn))

        rg = await client.call_tool("replay_game", {"pgn": pgn})
        print("replay_game ->", rg.data["white"], "vs", rg.data["black"],
              rg.data["result"], "| plies:", rg.data["total_plies"],
              "| first moves:", [m["san"] for m in rg.data["moves"][:4]])

        dp = await client.call_tool("describe_position", {
            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"})
        print("describe_position(start) -> turn:", dp.data["turn"],
              "legal:", dp.data["num_legal_moves"])

        pa = await client.call_tool("position_after_ply", {"pgn": pgn, "ply": 10})
        print("position_after_ply(10) -> last move:", pa.data["last_move_san"],
              "turn:", pa.data["turn"], "material balance:", pa.data["material"]["balance"])

        err = await client.call_tool("describe_position", {"fen": "garbage"})
        print("describe_position(garbage) ->", err.data.get("error"))

        print("\n--- Stockfish tools ---")
        es = await client.call_tool("engine_status", {})
        print("engine_status ->", es.data)
        ap = await client.call_tool("analyze_position", {
            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"})
        print("analyze_position(start) -> eval:", ap.data["eval"]["string"],
              "best:", ap.data["best_move"]["san"], "depth:", ap.data["depth"])
        bm = await client.call_tool("best_move", {"fen": "6k1/5ppp/8/8/8/8/8/R6K w - - 0 1"})
        print("best_move(mate-in-1) -> ", bm.data["best_move"], "eval:", bm.data["eval"]["string"])

        print("\n--- whole-game analysis (Phase 8) ---")
        scholars = ('[White "A"]\n[Black "B"]\n[Result "1-0"]\n\n'
                    "1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0\n")
        ag = await client.call_tool("analyze_game", {"pgn": scholars, "movetime": 0.15})
        print("analyze_game(scholars) -> white acc:", ag.data["players"]["white"]["accuracy"],
              "black acc:", ag.data["players"]["black"]["accuracy"],
              "| key moments:", [(k["ply"], k["classification"]) for k in ag.data["key_moments"]])

        rr = await client.call_tool("review_recent_game",
                                    {"username": "hikaru", "index": 0,
                                     "movetime": 0.06, "max_plies": 30})
        if "error" in rr.data:
            print("review_recent_game ->", rr.data["error"])
        else:
            print("review_recent_game -> reviewed:", rr.data.get("reviewed_player"),
                  "| your acc:", (rr.data.get("your_summary") or {}).get("accuracy"),
                  "| cached:", rr.data.get("cached"))

        e1 = await client.call_tool("analyze_game", {"pgn": "not a pgn"})
        print("analyze_game(bad pgn) ->", e1.data.get("error"))

        print("\n--- AI coaching tools (Phase 9) ---")
        bm = await client.call_tool("find_biggest_mistakes",
                                    {"username": "hikaru", "num_games": 3,
                                     "movetime": 0.06, "max_plies": 30, "top": 5})
        if "error" in bm.data:
            print("find_biggest_mistakes ->", bm.data["error"])
        else:
            print("find_biggest_mistakes -> analyzed:", bm.data["scope"]["games_analyzed"],
                  "| found:", len(bm.data["mistakes"]))

        rep = await client.call_tool("generate_coach_report",
                                     {"username": "hikaru", "num_games": 3,
                                      "movetime": 0.06, "max_plies": 30})
        if "error" in rep.data:
            print("generate_coach_report ->", rep.data["error"])
        else:
            print("generate_coach_report -> record:", rep.data["summary"]["record"],
                  "| confidence:", rep.data["confidence"],
                  "| weaknesses:", [f["area"] for f in rep.data["weaknesses"]],
                  "| caveats:", len(rep.data["caveats"]))

        e2 = await client.call_tool("generate_coach_report", {"username": "hikaru", "num_games": 99})
        print("generate_coach_report(bad num_games) ->", e2.data.get("error"))

    print("\nall tools OK")


if __name__ == "__main__":
    asyncio.run(run())
