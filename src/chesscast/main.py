from __future__ import annotations

import argparse
import hashlib
import html
import logging
import os
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from urllib.parse import quote

import chess
import chess.pgn
import requests
from PIL import Image, ImageStat

from chesscast.config import Settings
from chesscast.media import LiveOutput, create_thumbnail
from chesscast.metadata import make_metadata
from chesscast.move_quality import classify_moves
from chesscast.youtube import schedule_broadcast

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
GAME_DIR = Path(os.getenv("HISTORICAL_GAME_DIR", "/games"))
PLAYER_DIR = Path(os.getenv("CHESSCAST_PLAYER_DIR", "/app/assets/players"))
PORTRAIT_API = "https://commons.wikimedia.org/w/api.php"
COMMONS_HEADERS = {"User-Agent": "Chesscast/1.0 (historical chess replay)"}
GAME_CACHE_DIR = Path(os.getenv("HISTORICAL_GAME_CACHE", "/output/historical-games"))
GAME_HISTORY_PATH = Path(os.getenv("HISTORICAL_GAME_HISTORY", "/output/historical-games.seen"))
PLAYER_CREDITS_PATH = Path(os.getenv("PLAYER_CREDITS_FILE", "/output/player-image-credits.md"))
MAX_CACHED_GAMES = 4
DEFAULT_ARCHIVE_PLAYERS = (
    "Carlsen", "Kasparov", "Karpov", "Anand", "Ding", "Kramnik",
    "Fischer", "Capablanca", "Alekhine", "Tal", "Botvinnik", "Lasker",
    "Spassky", "Morphy", "Anderssen",
)
DEFAULT_ARCHIVE_URLS = tuple(
    "https://raw.githubusercontent.com/kinairi/chess-data/master/"
    f"data/players/{player}.pgn"
    for player in DEFAULT_ARCHIVE_PLAYERS
)


@dataclass(frozen=True)
class PreparedGame:
    path: Path
    game: chess.pgn.Game
    white: str
    black: str
    title: str
    subtitle: str
    annotations: list[str | None]
    signature: str
    youtube_title: str
    description: str


def load_game(path: Path) -> chess.pgn.Game:
    with path.open(encoding="utf-8") as source:
        game = chess.pgn.read_game(source)
    if game is None or game.errors:
        raise ValueError(f"Unable to read valid historical game from {path}")
    return game


def _player_slug(name: str) -> str:
    person = name.split("/", 1)[0].strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", person).strip("-")


def _portrait_search_name(name: str) -> str:
    parts = [part.strip() for part in name.split(",", 1)]
    return " ".join(reversed(parts)) if len(parts) == 2 else name.strip()


def _matches_player_portrait(name: str, title: str, description: str) -> bool:
    words = re.findall(r"[a-z]+", _portrait_search_name(name).lower())
    text = re.sub(r"<[^>]+>", " ", f"{title} {description}")
    searchable = re.sub(r"[^a-z]+", " ", html.unescape(text).lower())
    meaningful_words = [word for word in words if len(word) > 1]
    if not meaningful_words or not all(word in searchable.split() for word in meaningful_words):
        return False
    if len(meaningful_words) >= 2:
        return True
    return any(term in searchable for term in ("chess", "portrait", "photograph", "player"))


def _portrait_is_visible(source: Path | Image.Image) -> bool:
    try:
        image = Image.open(source).convert("RGB") if isinstance(source, Path) else source.convert("RGB")
        stats = ImageStat.Stat(image)
        return max(stats.mean) >= 18 and max(stats.stddev) >= 8
    except OSError:
        return False


def _record_portrait_credit(name: str, title: str, license_name: str,
                            artist: str) -> None:
    PLAYER_CREDITS_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_url = "https://commons.wikimedia.org/wiki/" + quote(title.replace(" ", "_"))
    credit = f"- {name}: [{title}]({file_url}), {artist or 'author not listed'}, {license_name}."
    existing = PLAYER_CREDITS_PATH.read_text(encoding="utf-8") if PLAYER_CREDITS_PATH.exists() else ""
    if file_url not in existing:
        with PLAYER_CREDITS_PATH.open("a", encoding="utf-8") as output:
            if not existing:
                output.write("# Dynamically Fetched Player Portrait Credits\n\n")
            output.write(credit + "\n")


def _fetch_portrait(name: str) -> None:
    slug = _player_slug(name)
    if not slug:
        return
    destination = PLAYER_DIR / f"{slug}.jpg"
    if destination.is_file():
        if _portrait_is_visible(destination):
            return
        destination.unlink(missing_ok=True)
    try:
        search_name = _portrait_search_name(name)
        response = requests.get(
            PORTRAIT_API,
            params={
                "action": "query", "generator": "search",
                "gsrsearch": f"{search_name} chess player",
                "gsrnamespace": 6, "gsrlimit": 8, "prop": "imageinfo",
                "iiprop": "url|extmetadata", "iiurlwidth": 330, "format": "json",
            },
            headers=COMMONS_HEADERS,
            timeout=(4, 10),
        )
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {}).values()
        for page in pages:
            info = page.get("imageinfo", [{}])[0]
            metadata = info.get("extmetadata", {})
            page_title = page.get("title", "")
            description = metadata.get("ImageDescription", {}).get("value", "")
            if not _matches_player_portrait(name, page_title, description):
                continue
            license_name = html.unescape(
                metadata.get("LicenseShortName", {}).get("value", "")
            )
            if not any(term in license_name.lower() for term in
                       ("public domain", "cc0", "cc by")):
                continue
            image_response = requests.get(
                info["thumburl"], headers=COMMONS_HEADERS, timeout=(4, 10)
            )
            image_response.raise_for_status()
            from io import BytesIO

            portrait = Image.open(BytesIO(image_response.content)).convert("RGB")
            if not _portrait_is_visible(portrait):
                continue
            PLAYER_DIR.mkdir(parents=True, exist_ok=True)
            portrait.save(destination, format="JPEG", quality=90)
            _record_portrait_credit(
                name, page_title, license_name,
                html.unescape(metadata.get("Artist", {}).get("value", "")),
            )
            logger.info("Prefetched verified portrait for %s", name)
            return
    except (KeyError, OSError, requests.RequestException, ValueError) as error:
        logger.info("No portrait fetched for %s: %s", name, error)


def prepare_game(path: Path, analyze: bool = True) -> PreparedGame:
    game = load_game(path)
    white = game.headers.get("White", "White")
    black = game.headers.get("Black", "Black")
    event = game.headers.get("Event", path.stem)
    title = {
        "Paris Opera House": "Morphy's Opera Game",
        "The Immortal Game": "The Immortal Game",
    }.get(event, event)
    site = game.headers.get("Site", "Chess archive").split(",", 1)[0]
    year = game.headers.get("Date", "").split(".", 1)[0]
    subtitle = " | ".join(value for value in (site, year) if value)
    signature = game_signature(game)
    youtube_title, description = make_metadata(
        title=title, white=white, black=black, event=event, site=site, year=year
    )
    _fetch_portrait(white)
    _fetch_portrait(black)
    annotations = classify_moves(game) if analyze else []
    return PreparedGame(path, game, white, black, title, subtitle, annotations,
                        signature, youtube_title, description)


def game_signature(game: chess.pgn.Game) -> str:
    headers = game.headers
    identity = "\0".join((
        headers.get("Event", ""), headers.get("Date", ""),
        headers.get("White", ""), headers.get("Black", ""),
        " ".join(move.uci() for move in game.mainline_moves()),
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def fetch_archive_game(seen: set[str]) -> Path | None:
    configured_urls = [
        value.strip()
        for value in os.getenv("HISTORICAL_PGN_URLS", "").split(",")
        if value.strip()
    ]
    urls = configured_urls or list(DEFAULT_ARCHIVE_URLS)
    for url in urls:
        if not url.startswith("https://"):
            logger.warning("Ignoring non-HTTPS historical PGN source")
            continue
        try:
            response = requests.get(url, timeout=(5, 30))
            response.raise_for_status()
            source = StringIO(response.text)
            while game := chess.pgn.read_game(source):
                if game.errors or game.end().ply() == 0:
                    continue
                if re.search(r"/Carlsen\.pgn(?:$|\?)", url, re.IGNORECASE):
                    year_text = game.headers.get("Date", "").split(".", 1)[0]
                    if not year_text.isdigit() or not 2008 <= int(year_text) <= datetime.now().year:
                        continue
                signature = game_signature(game)
                if signature in seen:
                    continue
                GAME_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                path = GAME_CACHE_DIR / f"{signature}.pgn"
                if not path.exists():
                    exporter = chess.pgn.StringExporter(
                        headers=True, variations=False, comments=True
                    )
                    path.write_text(game.accept(exporter) + "\n", encoding="utf-8")
                path.touch()
                remember_game_signature(signature)
                prune_game_cache()
                logger.info("Fetched a new archived game from public PGN source")
                return path
        except (OSError, requests.RequestException, ValueError) as error:
            logger.warning("Historical PGN source unavailable (%s)", type(error).__name__)
    return None


def cached_game_signatures() -> set[str]:
    signatures = set()
    if GAME_HISTORY_PATH.is_file():
        signatures.update(
            line.strip() for line in GAME_HISTORY_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    for path in GAME_CACHE_DIR.glob("*.pgn"):
        try:
            signatures.add(game_signature(load_game(path)))
        except (OSError, ValueError):
            logger.warning("Ignoring invalid cached PGN: %s", path.name)
    return signatures


def remember_game_signature(signature: str) -> None:
    GAME_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with GAME_HISTORY_PATH.open("a", encoding="utf-8") as history:
        history.write(signature + "\n")


def prune_game_cache() -> None:
    cached = sorted(
        GAME_CACHE_DIR.glob("*.pgn"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for old_path in cached[MAX_CACHED_GAMES:]:
        old_path.unlink(missing_ok=True)


def prepare_next_game(paths: list[Path], index: int, seen: set[str]) -> PreparedGame:
    archive_path = fetch_archive_game(seen)
    if archive_path is not None:
        return prepare_game(archive_path)

    for offset in range(len(paths)):
        path = paths[(index + offset) % len(paths)]
        signature = game_signature(load_game(path))
        if signature not in seen:
            remember_game_signature(signature)
            return prepare_game(path)
    return prepare_game(paths[index % len(paths)])


def game_files() -> list[Path]:
    paths = list(GAME_DIR.glob("*.pgn"))
    priority = {"morphy-opera-1858.pgn": 0, "anderssen-immortal-1851.pgn": 1}
    return sorted(paths, key=lambda path: (priority.get(path.name, 2), path.name))


def announce_when_ready(future: Future[PreparedGame], outputs: list[LiveOutput]) -> bool:
    if not future.done():
        return False
    upcoming = future.result()
    for output in outputs:
        output.set_next_game(upcoming.title, upcoming.white, upcoming.black)
    return True


def run_playlist(settings: Settings, paths: list[Path], first: PreparedGame,
                 max_games: int = 0, max_runtime_seconds: float = 0) -> None:
    move_delay = max(0.1, float(os.getenv("HISTORICAL_MOVE_DELAY", "8")))
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="game-prefetch")
    current_index = paths.index(first.path) if first.path in paths else -1
    current = first
    stream_started = time.monotonic()
    current_analysis = executor.submit(classify_moves, current.game)
    next_index = (current_index + 1) % len(paths)
    prune_game_cache()
    seen = cached_game_signatures()
    seen.add(current.signature)
    next_game = executor.submit(prepare_next_game, paths, next_index, set(seen))
    thumbnail = Path(os.getenv("THUMBNAIL_OUTPUT", "/output/chesscast-thumbnail.png"))
    create_thumbnail(thumbnail, current.title, current.white, current.black,
                     current.game.board(), current.subtitle)
    outputs = [
        LiveOutput(settings.youtube_rtmp_url, current.title,
                   settings.music_file, settings.music_volume,
                   current.subtitle, layout="landscape")
    ]
    if settings.youtube_vertical_rtmp_url:
        outputs.append(
            LiveOutput(settings.youtube_vertical_rtmp_url, current.title,
                       settings.music_file, settings.music_volume,
                       current.subtitle, layout="vertical")
        )
    for output in outputs:
        output.update(current.game.board(), current.white, current.black, None,
                      current.title, current.subtitle)
    try:
        for output in outputs:
            output.start()
        current_annotations = current_analysis.result()
        games_completed = 0
        while True:
            logger.info("Playing %s: %s vs %s", current.title, current.white, current.black)
            board = current.game.board()
            time.sleep(move_delay)
            announced = announce_when_ready(next_game, outputs)
            for index, move in enumerate(current.game.mainline_moves()):
                san = board.san(move)
                board.push(move)
                quality = (current_annotations[index]
                           if index < len(current_annotations) else None)
                for output in outputs:
                    output.update(board, current.white, current.black, move,
                                  current.title, current.subtitle, quality)
                logger.info("Historical replay: %s%s", san,
                            f" [{quality}]" if quality else "")
                if not announced:
                    announced = announce_when_ready(next_game, outputs)
                time.sleep(move_delay)
            games_completed += 1
            logger.info("Completed historical game %d%s", games_completed,
                        f"/{max_games}" if max_games else "")
            if max_games and games_completed >= max_games:
                logger.info("Game limit reached; stopping both RTMP outputs cleanly")
                break
            if (max_runtime_seconds
                    and time.monotonic() - stream_started >= max_runtime_seconds):
                logger.info("Runtime limit reached at game boundary; stopping both outputs")
                break
            # The encoder and music keep running; only the board/game metadata changes.
            current = next_game.result()
            seen.add(current.signature)
            current_index = paths.index(current.path) if current.path in paths else next_index
            next_index = (current_index + 1) % len(paths)
            next_game = executor.submit(prepare_next_game, paths, next_index, set(seen))
            current_annotations = current.annotations
            for output in outputs:
                output.update(current.game.board(), current.white, current.black, None,
                              current.title, current.subtitle)
    except KeyboardInterrupt:
        logger.info("Stopping archived game playlist")
    finally:
        for output in outputs:
            output.stop()
        executor.shutdown(wait=False, cancel_futures=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Historical chess livestream")
    parser.add_argument("--schedule-at",
                        help="Schedule in ISO-8601 with timezone, then stream at that time")
    parser.add_argument("--privacy", choices=("private", "unlisted", "public"),
                        default="private")
    parser.add_argument("--no-ai-metadata", action="store_true",
                        help="Use the accurate template instead of optional OpenAI metadata")
    parser.add_argument("--max-games", type=int,
                        default=int(os.getenv("MAX_GAMES", "0")),
                        help="Stop cleanly after this many completed games; 0 means unlimited")
    parser.add_argument("--max-runtime-seconds", type=float,
                        default=float(os.getenv("MAX_STREAM_SECONDS", "0")),
                        help="Stop at the next game boundary after this runtime; 0 means unlimited")
    args = parser.parse_args()
    if args.max_games < 0 or args.max_runtime_seconds < 0:
        parser.error("game and runtime limits must be zero or greater")

    settings = Settings.from_env()
    paths = game_files()
    if not paths:
        raise ValueError(f"No historical PGN files found in {GAME_DIR}")
    seen = cached_game_signatures()
    first_path = fetch_archive_game(seen) or paths[0]
    if first_path in paths:
        remember_game_signature(game_signature(load_game(first_path)))
    first = prepare_game(first_path, analyze=False)

    if args.schedule_at:
        scheduled_at = datetime.fromisoformat(args.schedule_at.replace("Z", "+00:00"))
        if scheduled_at.tzinfo is None:
            raise ValueError("--schedule-at must include a timezone")
        title, description = make_metadata(
            title=first.title,
            white=first.white,
            black=first.black,
            event=first.game.headers.get("Event", first.title),
            site=first.game.headers.get("Site", "Chess archive"),
            year=first.game.headers.get("Date", "").split(".", 1)[0],
            generate_with_ai=not args.no_ai_metadata,
        )
        first = replace(first, title=title, youtube_title=title, description=description)
        thumbnail = Path(os.getenv("THUMBNAIL_OUTPUT", "/output/chesscast-thumbnail.png"))
        create_thumbnail(thumbnail, first.title, first.white, first.black,
                         first.game.board(), first.subtitle)
        stream_key = settings.youtube_rtmp_url.rsplit("/", 1)[-1]
        url = schedule_broadcast(
            stream_key=stream_key,
            title=title,
            description=description,
            scheduled_at=scheduled_at,
            thumbnail=thumbnail,
            privacy=args.privacy,
        )
        logger.info("Scheduled YouTube broadcast: %s", url)
        while datetime.now(timezone.utc) < scheduled_at.astimezone(timezone.utc):
            time.sleep(min(60, (scheduled_at - datetime.now(scheduled_at.tzinfo)).total_seconds()))

    run_playlist(settings, paths, first, max_games=args.max_games,
                 max_runtime_seconds=args.max_runtime_seconds)


if __name__ == "__main__":
    main()