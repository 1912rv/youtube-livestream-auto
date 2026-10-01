from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import chess
import chess.engine
import chess.pgn

logger = logging.getLogger(__name__)


def classify_moves(game: chess.pgn.Game) -> list[str | None]:
    """Score a PGN with Stockfish; labels are a lightweight heuristic, not Chess.com ratings."""
    engine_path = os.getenv("STOCKFISH_PATH") or shutil.which("stockfish")
    if not engine_path and Path("/usr/games/stockfish").is_file():
        engine_path = "/usr/games/stockfish"
    moves = list(game.mainline_moves())
    if not engine_path:
        logger.warning("Stockfish unavailable; streaming without move-quality labels")
        return [None] * len(moves)

    depth = max(8, int(os.getenv("STOCKFISH_DEPTH", "12")))
    labels: list[str | None] = []
    board = game.board()
    previous_error = False
    nodes = list(game.mainline())
    try:
        engine = chess.engine.SimpleEngine.popen_uci(engine_path)
    except (OSError, chess.engine.EngineError) as error:
        logger.warning("Could not start Stockfish (%s); streaming without labels", error)
        return [None] * len(moves)

    try:
        engine.configure({"Threads": 1, "Hash": 64})
        for index, move in enumerate(moves):
            comment = nodes[index].comment.lower() if index < len(nodes) else ""
            if "[%book]" in comment or "[book]" in comment:
                labels.append("BOOK")
                board.push(move)
                previous_error = False
                continue

            mover = board.turn
            moving_piece = board.piece_at(move.from_square)
            before = engine.analyse(board, chess.engine.Limit(depth=depth))
            best_move = before.get("pv", [None])[0]
            before_score = before["score"].pov(mover).score(mate_score=100_000)
            next_board = board.copy(stack=False)
            next_board.push(move)
            after = engine.analyse(next_board, chess.engine.Limit(depth=depth))
            after_score = after["score"].pov(mover).score(mate_score=100_000)
            loss = before_score - after_score

            if loss >= 300:
                quality = "BLUNDER"
            elif loss >= 150:
                quality = "MISTAKE"
            elif previous_error and move != best_move:
                quality = "MISS"
            elif loss >= 75:
                quality = "INACCURACY"
            elif best_move == move:
                response = after.get("pv", [None])[0]
                is_sacrifice = (
                    moving_piece is not None
                    and moving_piece.piece_type in {
                        chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN
                    }
                    and response is not None
                    and next_board.is_capture(response)
                    and response.to_square == move.to_square
                )
                quality = "BRILLIANT" if is_sacrifice and loss < 15 else "BEST MOVE"
            elif loss <= 10:
                quality = "GREAT MOVE"
            elif loss <= 30:
                quality = "EXCELLENT"
            elif loss <= 75:
                quality = "GOOD"
            else:
                quality = "INACCURACY"

            labels.append(quality)
            previous_error = loss >= 150
            board.push(move)
    except (chess.engine.EngineError, KeyError, TypeError, ValueError) as error:
        logger.warning("Stockfish analysis stopped (%s); later moves will be unlabelled", error)
        labels.extend([None] * (len(moves) - len(labels)))
    finally:
        engine.quit()
    return labels
