from __future__ import annotations

import json
import os

import requests


def make_metadata(
    *, title: str, white: str, black: str, event: str, site: str, year: str,
    generate_with_ai: bool = False,
) -> tuple[str, str]:
    fallback_title = f"{title}: {white} vs {black} ({year})"[:100]
    fallback_description = (
        f"Replay of {event}, featuring {white} as White and {black} as Black.\n\n"
        f"Played at {site} in {year}. This archived PGN is replayed on an original "
        "chessboard graphic with Stockfish move-quality analysis and instrumental "
        "background music. It is a historical game replay, not a live tournament game.\n\n"
        "Move-quality labels are engine estimates and may differ from human analysis."
    )

    api_key = os.getenv("OPENAI_KEY") or os.getenv("OPENAI_API_KEY")
    if not generate_with_ai or not api_key:
        return fallback_title, fallback_description

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Write concise, accurate YouTube metadata for an archived "
                            "chess replay. Never call it live, invent an event detail, "
                            "or use clickbait. Return JSON with title (max 95 chars) "
                            "and description."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps({
                            "game": event, "white": white, "black": black,
                            "site": site, "year": year,
                            "video": "PGN replay with Stockfish labels and instrumental music",
                        }),
                    },
                ],
                "temperature": 0.4,
            },
            timeout=(5, 25),
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        generated = json.loads(content)
        generated_title = str(generated["title"]).strip()[:100]
        generated_description = str(generated["description"]).strip()
        if generated_title and generated_description:
            return generated_title, generated_description
    except (KeyError, TypeError, ValueError, requests.RequestException):
        pass
    return fallback_title, fallback_description
