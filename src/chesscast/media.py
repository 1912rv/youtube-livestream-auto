from __future__ import annotations

import io
import logging
import math
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import cairosvg
import chess
import chess.svg
from PIL import Image, ImageDraw, ImageFont, ImageOps

logger = logging.getLogger(__name__)
STREAM_LAYOUT = os.getenv("STREAM_LAYOUT", "vertical").lower()
if STREAM_LAYOUT not in {"vertical", "landscape"}:
    raise ValueError("STREAM_LAYOUT must be 'vertical' or 'landscape'")
LANDSCAPE_WIDTH, LANDSCAPE_HEIGHT, LANDSCAPE_BOARD_SIZE = 1280, 720, 380
VERTICAL_WIDTH, VERTICAL_HEIGHT, VERTICAL_BOARD_SIZE = 720, 1280, 600
FPS = 24
WIDTH, HEIGHT, BOARD_SIZE = (
    (VERTICAL_WIDTH, VERTICAL_HEIGHT, VERTICAL_BOARD_SIZE)
    if STREAM_LAYOUT == "vertical"
    else (LANDSCAPE_WIDTH, LANDSCAPE_HEIGHT, LANDSCAPE_BOARD_SIZE)
)
BACKGROUND = Path(os.getenv("CHESSCAST_BACKGROUND", "/app/assets/chess_live_thumbnail.png"))
PLAYER_DIR = Path(os.getenv("CHESSCAST_PLAYER_DIR", "/app/assets/players"))
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
MOVE_LABEL_COLORS = {
    "BRILLIANT": "#13b8a6",
    "GREAT MOVE": "#5796c6",
    "BEST MOVE": "#a4c84c",
    "EXCELLENT": "#8fba49",
    "GOOD": "#95a88e",
    "BOOK": "#b29470",
    "INACCURACY": "#f5bf43",
    "MISTAKE": "#e99233",
    "MISS": "#ed7165",
    "BLUNDER": "#d64242",
}
MOVE_BADGES = {
    "BRILLIANT": "!!",
    "GREAT MOVE": "!",
    "BEST MOVE": "star",
    "EXCELLENT": "thumb",
    "GOOD": "check",
    "BOOK": "book",
    "INACCURACY": "?!",
    "MISTAKE": "?",
    "MISS": "cross",
    "BLUNDER": "??",
}


def _draw_move_badge(draw: ImageDraw.ImageDraw, center: tuple[int, int],
                     quality: str) -> None:
    badge = MOVE_BADGES[quality]
    color = MOVE_LABEL_COLORS[quality]
    draw.ellipse((center[0] - 11, center[1] - 11,
                  center[0] + 11, center[1] + 11), fill=color)
    ink = "#f8f8f4"
    if badge == "star":
        points = []
        for index in range(10):
            angle = -math.pi / 2 + index * math.pi / 5
            radius = 8 if index % 2 == 0 else 3.5
            points.append((center[0] + math.cos(angle) * radius,
                           center[1] + math.sin(angle) * radius))
        draw.polygon(points, fill=ink)
    elif badge == "thumb":
        x, y = center
        draw.polygon([(x - 5, y - 1), (x - 2, y - 3), (x + 1, y - 8),
                      (x + 4, y - 7), (x + 3, y - 2), (x + 7, y - 2),
                      (x + 8, y), (x + 6, y + 7), (x - 5, y + 7)], fill=ink)
        draw.rectangle((x - 8, y - 1, x - 6, y + 7), fill=ink)
    elif badge == "check":
        x, y = center
        draw.line((x - 7, y, x - 2, y + 5, x + 7, y - 6), fill=ink, width=3)
    elif badge == "book":
        x, y = center
        draw.polygon([(x - 8, y - 6), (x - 2, y - 5), (x, y - 3),
                      (x, y + 7), (x - 3, y + 5), (x - 8, y + 5)], fill=ink)
        draw.polygon([(x + 8, y - 6), (x + 2, y - 5), (x, y - 3),
                      (x, y + 7), (x + 3, y + 5), (x + 8, y + 5)], fill=ink)
    elif badge == "cross":
        x, y = center
        draw.line((x - 5, y - 5, x + 5, y + 5), fill=ink, width=3)
        draw.line((x + 5, y - 5, x - 5, y + 5), fill=ink, width=3)
    else:
        badge_font = ImageFont.truetype(f"{FONT_DIR}/DejaVuSans-Bold.ttf", 11)
        draw.text(center, badge, font=badge_font, fill=ink, anchor="mm")


def _landscape_scene(
    title: str,
    white: str,
    black: str,
    board: chess.Board,
    last_move: chess.Move | None,
    subtitle: str = "",
    next_info: tuple[str, str, str] | None = None,
    quality: str | None = None,
) -> Image.Image:
    try:
        image = Image.open(BACKGROUND).convert("RGB")
    except OSError:
        image = Image.new("RGB", (LANDSCAPE_WIDTH, LANDSCAPE_HEIGHT), "#111820")
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 45, 1230, 670), fill="#111a1d")
    for x in range(64, 1230, 64):
        draw.line((x, 45, x, 670), fill="#192528", width=1)
    for y in range(64, 670, 64):
        draw.line((50, y, 1230, y), fill="#192528", width=1)

    svg = chess.svg.board(board, size=LANDSCAPE_BOARD_SIZE, lastmove=last_move,
                          coordinates=True)
    board_png = cairosvg.svg2png(
        bytestring=svg.encode(), output_width=LANDSCAPE_BOARD_SIZE,
        output_height=LANDSCAPE_BOARD_SIZE,
    )
    image.paste(Image.open(io.BytesIO(board_png)).convert("RGB"), (450, 155))
    if quality and last_move:
        cell = (LANDSCAPE_BOARD_SIZE - 28) / 8
        left = 450 + 14 + chess.square_file(last_move.to_square) * cell
        top = 155 + 14 + (7 - chess.square_rank(last_move.to_square)) * cell
        center = (int(left + cell - 9), int(top + 10))
        _draw_move_badge(draw, center, quality)

    tag = ImageFont.truetype(f"{FONT_DIR}/DejaVuSans-Bold.ttf", 18)
    heading = ImageFont.truetype(f"{FONT_DIR}/DejaVuSans-Bold.ttf", 27)
    body = ImageFont.truetype(f"{FONT_DIR}/DejaVuSans.ttf", 21)
    small = ImageFont.truetype(f"{FONT_DIR}/DejaVuSans.ttf", 16)

    draw.text((640, 15), title.upper()[:28], font=heading,
              fill="#f5f3ed", anchor="mt")
    draw.text((640, 48), subtitle.upper()[:34], font=small,
              fill="#d4f35b", anchor="mt")

    def portrait(name: str, xy: tuple[int, int], size: int) -> None:
        person = name.split("/", 1)[0].strip()
        slug = re.sub(r"[^a-z0-9]+", "-", person.lower()).strip("-")
        portrait_path = PLAYER_DIR / f"{slug}.jpg"
        initials = "".join(part[0] for part in person.split()[:2]).upper() or "?"
        try:
            photo = ImageOps.fit(Image.open(portrait_path).convert("RGB"), (size, size))
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
            image.paste(photo, xy, mask)
        except OSError:
            draw.ellipse((*xy, xy[0] + size, xy[1] + size), fill="#d4f35b")
            draw.text((xy[0] + size // 2, xy[1] + size // 2), initials,
                      font=tag, fill="#111a1d", anchor="mm")

    # This compact center column remains visible in YouTube's 9:16 auto-crop.
    portrait(black, (457, 82), 52)
    draw.text((523, 81), "BLACK", font=small, fill="#d4f35b")
    draw.text((523, 103), black[:31].upper(), font=tag, fill="#f5f3ed")
    portrait(white, (457, 562), 52)
    draw.text((523, 563), "WHITE", font=small, fill="#d4f35b")
    draw.text((523, 585), white[:31].upper(), font=tag, fill="#f5f3ed")

    # Full player cards are visible in landscape; the center column stays uncluttered.
    draw.rounded_rectangle((64, 125, 420, 590), radius=12, fill="#1c292c")
    draw.rounded_rectangle((860, 125, 1216, 590), radius=12, fill="#1c292c")
    portrait(white, (172, 174), 140)
    portrait(black, (968, 174), 140)
    draw.text((242, 345), "WHITE", font=small, fill="#d4f35b", anchor="mt")
    draw.text((242, 376), white[:24].upper(), font=tag, fill="#f5f3ed", anchor="mt")
    draw.text((1038, 345), "BLACK", font=small, fill="#d4f35b", anchor="mt")
    black_names = [name.strip() for name in black.split("/")]
    for index, name in enumerate(black_names[:2]):
        draw.text((1038, 376 + index * 34), name[:24].upper(), font=tag,
                  fill="#f5f3ed", anchor="mt")
    if len(black_names) > 1:
        draw.text((1038, 451), "PLAYED AS A TEAM", font=small,
                  fill="#aab7ba", anchor="mt")

    turn = "WHITE TO MOVE" if board.turn == chess.WHITE else "BLACK TO MOVE"
    if next_info:
        next_font_size = 13
        next_font = ImageFont.truetype(f"{FONT_DIR}/DejaVuSans.ttf", next_font_size)
        next_title, next_white, next_black = next_info
        matchup = f"{next_white.upper()} VS {next_black.upper()}"
        while draw.textlength(matchup, font=next_font) > 370 and next_font_size > 10:
            next_font_size -= 1
            next_font = ImageFont.truetype(
                f"{FONT_DIR}/DejaVuSans.ttf", next_font_size
            )
        while draw.textlength(matchup, font=next_font) > 370 and len(matchup) > 8:
            matchup = matchup[:-4] + "..."
        draw.text((640, 617), f"NEXT: {next_title.upper()[:28]}", font=next_font,
                  fill="#d4f35b", anchor="mt")
        draw.text((640, 636), matchup, font=next_font, fill="#f5f3ed", anchor="mt")
        prefix = f"MOVE {board.fullmove_number}  |  {turn}  |  "
        label = quality.title() if quality else "PGN REPLAY"
        full_line = prefix + label
        line_left = (LANDSCAPE_WIDTH - draw.textlength(full_line, font=small)) / 2
        draw.text((line_left, 661), prefix, font=small, fill="#aab7ba")
        draw.text((line_left + draw.textlength(prefix, font=small), 661), label,
                  font=small, fill=MOVE_LABEL_COLORS.get(quality, "#aab7ba"))
    else:
        prefix = f"MOVE {board.fullmove_number}  |  {turn}  |  "
        label = quality.title() if quality else "PGN REPLAY"
        full_line = prefix + label
        line_left = (LANDSCAPE_WIDTH - draw.textlength(full_line, font=small)) / 2
        draw.text((line_left, 644), prefix, font=small, fill="#aab7ba")
        draw.text((line_left + draw.textlength(prefix, font=small), 644), label,
                  font=small, fill=MOVE_LABEL_COLORS.get(quality, "#aab7ba"))
    return image


def _vertical_scene(
    title: str,
    white: str,
    black: str,
    board: chess.Board,
    last_move: chess.Move | None,
    subtitle: str = "",
    next_info: tuple[str, str, str] | None = None,
    quality: str | None = None,
) -> Image.Image:
    image = Image.new("RGB", (VERTICAL_WIDTH, VERTICAL_HEIGHT), "#111820")
    draw = ImageDraw.Draw(image)
    draw.rectangle((24, 25, VERTICAL_WIDTH - 24, VERTICAL_HEIGHT - 24),
                   outline="#344347", width=2)
    for y in range(110, VERTICAL_HEIGHT - 40, 64):
        draw.line((26, y, VERTICAL_WIDTH - 26, y), fill="#1a282b", width=1)

    title_font = ImageFont.truetype(f"{FONT_DIR}/DejaVuSans-Bold.ttf", 29)
    player_font = ImageFont.truetype(f"{FONT_DIR}/DejaVuSans-Bold.ttf", 23)
    label_font = ImageFont.truetype(f"{FONT_DIR}/DejaVuSans-Bold.ttf", 16)
    body_font = ImageFont.truetype(f"{FONT_DIR}/DejaVuSans.ttf", 17)
    small_font = ImageFont.truetype(f"{FONT_DIR}/DejaVuSans.ttf", 14)

    draw.text((WIDTH // 2, 42), title.upper()[:32], font=title_font,
              fill="#f5f3ed", anchor="mt")
    draw.text((WIDTH // 2, 82), subtitle.upper()[:38], font=small_font,
              fill="#d4f35b", anchor="mt")

    def portrait(name: str, xy: tuple[int, int], size: int) -> None:
        person = name.split("/", 1)[0].strip()
        slug = re.sub(r"[^a-z0-9]+", "-", person.lower()).strip("-")
        initials = "".join(part[0] for part in person.split()[:2]).upper() or "?"
        try:
            photo = ImageOps.fit(
                Image.open(PLAYER_DIR / f"{slug}.jpg").convert("RGB"),
                (size, size),
            )
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
            image.paste(photo, xy, mask)
        except OSError:
            draw.ellipse((*xy, xy[0] + size, xy[1] + size), fill="#d4f35b")
            draw.text((xy[0] + size // 2, xy[1] + size // 2), initials,
                      font=label_font, fill="#111a1d", anchor="mm")

    def player_row(name: str, color_name: str, top: int) -> None:
        draw.rounded_rectangle((45, top, VERTICAL_WIDTH - 45, top + 100), radius=12,
                               fill="#1c292c")
        portrait(name, (64, top + 12), 76)
        draw.text((158, top + 14), color_name, font=small_font,
                  fill="#d4f35b")
        draw.text((158, top + 42), name.upper()[:35], font=player_font,
                  fill="#f5f3ed")

    player_row(black, "BLACK", 120)
    board_x = (VERTICAL_WIDTH - VERTICAL_BOARD_SIZE) // 2
    board_y = 242
    svg = chess.svg.board(board, size=VERTICAL_BOARD_SIZE, lastmove=last_move,
                          coordinates=True)
    board_png = cairosvg.svg2png(bytestring=svg.encode(),
                                 output_width=VERTICAL_BOARD_SIZE,
                                 output_height=VERTICAL_BOARD_SIZE)
    image.paste(Image.open(io.BytesIO(board_png)).convert("RGB"),
                (board_x, board_y))
    draw = ImageDraw.Draw(image)

    if quality and last_move:
        cell = (VERTICAL_BOARD_SIZE - 28) / 8
        left = board_x + 14 + chess.square_file(last_move.to_square) * cell
        top = board_y + 14 + (7 - chess.square_rank(last_move.to_square)) * cell
        _draw_move_badge(draw, (int(left + cell - 10), int(top + 10)), quality)

    turn = "WHITE TO MOVE" if board.turn == chess.WHITE else "BLACK TO MOVE"
    prefix = f"MOVE {board.fullmove_number}  |  {turn}  |  "
    label = quality.title() if quality else "PGN REPLAY"
    full_line = prefix + label
    font = ImageFont.truetype(f"{FONT_DIR}/DejaVuSans.ttf", 16)
    start_x = (VERTICAL_WIDTH - draw.textlength(full_line, font=font)) / 2
    draw.text((start_x, 855), prefix, font=font, fill="#aab7ba")
    draw.text((start_x + draw.textlength(prefix, font=font), 855), label,
              font=font, fill=MOVE_LABEL_COLORS.get(quality, "#aab7ba"))

    player_row(white, "WHITE", 890)
    if next_info:
        next_title, next_white, next_black = next_info
        draw.rounded_rectangle((45, 1015, VERTICAL_WIDTH - 45, 1160), radius=12,
                               fill="#1c292c")
        draw.text((VERTICAL_WIDTH // 2, 1032), "COMING NEXT", font=label_font,
                  fill="#d4f35b", anchor="mt")
        draw.text((VERTICAL_WIDTH // 2, 1062), next_title.upper()[:30], font=player_font,
                  fill="#f5f3ed", anchor="mt")
        matchup = f"{next_white.upper()} VS {next_black.upper()}"
        matchup_font = body_font
        while draw.textlength(matchup, font=matchup_font) > VERTICAL_WIDTH - 100:
            matchup_font = ImageFont.truetype(
                f"{FONT_DIR}/DejaVuSans.ttf", max(12, matchup_font.size - 1)
            )
            if matchup_font.size == 12:
                break
        draw.text((VERTICAL_WIDTH // 2, 1105), matchup, font=matchup_font,
                  fill="#aab7ba", anchor="mt")

    draw.text((VERTICAL_WIDTH // 2, 1202), "HISTORICAL PGN  |  INSTRUMENTAL AUDIO",
              font=small_font, fill="#82919c", anchor="mt")
    return image


def _scene(
    title: str,
    white: str,
    black: str,
    board: chess.Board,
    last_move: chess.Move | None,
    subtitle: str = "",
    next_info: tuple[str, str, str] | None = None,
    quality: str | None = None,
    layout: str | None = None,
) -> Image.Image:
    selected_layout = layout or STREAM_LAYOUT
    if selected_layout not in {"vertical", "landscape"}:
        raise ValueError("layout must be 'vertical' or 'landscape'")
    renderer = _vertical_scene if selected_layout == "vertical" else _landscape_scene
    return renderer(title, white, black, board, last_move, subtitle, next_info, quality)


def create_thumbnail(
    path: Path,
    title: str,
    white: str,
    black: str,
    board: chess.Board,
    subtitle: str = "",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _landscape_scene(title, white, black, board, None, subtitle).save(
        path, format="PNG", optimize=True
    )
    return path


class LiveOutput:
    def __init__(self, rtmp_url: str, title: str, music_file: str, music_volume: float,
                 subtitle: str = "", layout: str | None = None):
        self.layout = layout or STREAM_LAYOUT
        if self.layout not in {"vertical", "landscape"}:
            raise ValueError("layout must be 'vertical' or 'landscape'")
        self.width, self.height = (
            (VERTICAL_WIDTH, VERTICAL_HEIGHT) if self.layout == "vertical"
            else (LANDSCAPE_WIDTH, LANDSCAPE_HEIGHT)
        )
        self.rtmp_url = rtmp_url
        self.title = title
        self.subtitle = subtitle
        self.music_file = music_file
        self.music_volume = music_volume
        self.stop_event = threading.Event()
        self.frame_lock = threading.Lock()
        self.board = chess.Board()
        self.white, self.black = "Paul Morphy", "Duke Karl / Count Isouard"
        self.last_move: chess.Move | None = None
        self.quality: str | None = None
        self.next_info: tuple[str, str, str] | None = None
        self.frame = _scene(self.title, self.white, self.black, self.board, None,
                            self.subtitle, layout=self.layout).tobytes()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="chesscast-")
        self.video_fifo = Path(self.temp_dir.name) / "video.raw"
        os.mkfifo(self.video_fifo)
        self.process: subprocess.Popen[bytes] | None = None
        self.writer: threading.Thread | None = None

    def start(self) -> None:
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-thread_queue_size", "8", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-video_size", f"{self.width}x{self.height}", "-framerate", str(FPS),
            "-i", str(self.video_fifo), "-thread_queue_size", "8", "-re",
            "-stream_loop", "-1", "-i", self.music_file,
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
            "-preset", "veryfast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
            "-r", str(FPS), "-g", str(FPS * 2), "-b:v", "2500k",
            "-minrate", "2500k", "-maxrate", "2500k", "-bufsize", "5000k",
            "-color_primaries", "bt709", "-color_trc", "bt709",
            "-colorspace", "bt709", "-color_range", "tv",
            "-x264-params", "nal-hrd=cbr:force-cfr=1",
            "-af", f"volume={self.music_volume},alimiter=limit=0.95",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-f", "flv", self.rtmp_url,
        ]
        self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
        self.writer = threading.Thread(target=self._write_video, daemon=True)
        self.writer.start()
        logger.info("Publishing %s %dx%d stream: %s", self.layout,
                self.width, self.height, self.title)

    def update(self, board: chess.Board, white: str, black: str,
               last_move: chess.Move | None, title: str | None = None,
               subtitle: str | None = None, quality: str | None = None) -> None:
        with self.frame_lock:
            self.board, self.white, self.black = board.copy(), white, black
            self.last_move = last_move
            if title is not None:
                if title != self.title:
                    self.next_info = None
                self.title = title
            if subtitle is not None:
                self.subtitle = subtitle
            self.quality = quality
            self.frame = _scene(self.title, white, black, self.board, last_move,
                                self.subtitle, self.next_info, self.quality,
                                self.layout).tobytes()

    def set_next_game(self, title: str, white: str, black: str) -> None:
        with self.frame_lock:
            self.next_info = (title, white, black)
            self.frame = _scene(self.title, self.white, self.black, self.board,
                                self.last_move, self.subtitle, self.next_info,
                                self.quality, self.layout).tobytes()

    def stop(self) -> None:
        self.stop_event.set()
        try:
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        self.process.kill()
                    except ProcessLookupError:
                        pass
                    self.process.wait()
        finally:
            if self.writer is not None:
                self.writer.join(timeout=2)
            self.temp_dir.cleanup()

    def _write_video(self) -> None:
        interval = 1 / FPS
        try:
            with self.video_fifo.open("wb", buffering=0) as stream:
                next_frame = time.monotonic()
                while not self.stop_event.is_set():
                    with self.frame_lock:
                        frame = self.frame
                    stream.write(frame)
                    next_frame += interval
                    self.stop_event.wait(max(0, next_frame - time.monotonic()))
        except (BrokenPipeError, OSError) as error:
            if not self.stop_event.is_set():
                logger.error("Video output stopped: %s", error)
