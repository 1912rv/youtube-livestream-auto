from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_INGEST_URL = "rtmp://a.rtmp.youtube.com/live2"


@dataclass(frozen=True)
class Settings:
    youtube_rtmp_url: str
    youtube_vertical_rtmp_url: str | None
    music_file: str
    music_volume: float

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        ingest_url = os.getenv("YOUTUBE_STREAM_URL", DEFAULT_INGEST_URL).rstrip("/")
        primary_value = (
            os.getenv("YOUTUBE_STREAM_KEY", "").strip()
            or os.getenv("YOUTUBE_RTMP_URL", "").strip()
        )
        if not primary_value:
            raise ValueError("Set YOUTUBE_STREAM_KEY or YOUTUBE_RTMP_URL in .env")
        rtmp_url = (
            primary_value if primary_value.lower().startswith(("rtmp://", "rtmps://"))
            else f"{ingest_url}/{primary_value}"
        )

        vertical_value = os.getenv("YOUTUBE_VERTICAL_RTMP_URL", "").strip()
        vertical_ingest = os.getenv("YOUTUBE_VERTICAL_STREAM_URL", ingest_url).rstrip("/")
        vertical_rtmp_url = None
        if vertical_value:
            vertical_rtmp_url = (
                vertical_value
                if vertical_value.lower().startswith(("rtmp://", "rtmps://"))
                else f"{vertical_ingest}/{vertical_value}"
            )

        default_music = Path(__file__).resolve().parents[2] / "assets" / "chesscast_theme.wav"
        music_file = Path(os.getenv("MUSIC_FILE", str(default_music))).expanduser()
        if not music_file.is_file():
            raise ValueError(f"MUSIC_FILE does not point to a readable file: {music_file}")
        music_volume = float(os.getenv("MUSIC_VOLUME", "0.22"))
        if not 0 <= music_volume <= 1:
            raise ValueError("MUSIC_VOLUME must be between 0 and 1")
        return cls(str(rtmp_url), vertical_rtmp_url, str(music_file), music_volume)
