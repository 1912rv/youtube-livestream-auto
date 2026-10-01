from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube"]


def _youtube_client():
    client_secrets = Path(os.getenv("YOUTUBE_CLIENT_SECRETS", "client_secret.json"))
    token_path = Path(os.getenv("YOUTUBE_TOKEN_FILE", "/output/youtube-token.json"))
    if not client_secrets.is_file():
        raise ValueError(
            "Set YOUTUBE_CLIENT_SECRETS to a YouTube Data API OAuth client JSON "
            "file. A stream key cannot schedule broadcasts or update metadata."
        )

    credentials = None
    if token_path.is_file():
        credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
            credentials = flow.run_local_server(
                host="localhost",
                bind_addr="0.0.0.0",
                port=int(os.getenv("YOUTUBE_OAUTH_PORT", "8080")),
                open_browser=False,
                authorization_prompt_message="Authorize YouTube in a browser: {url}",
            )
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")
        token_path.chmod(0o600)
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def schedule_broadcast(
    *, stream_key: str, title: str, description: str, scheduled_at: datetime,
    thumbnail: Path, privacy: str = "private",
) -> str:
    if scheduled_at.tzinfo is None:
        raise ValueError("Schedule time must include a timezone, e.g. 2026-10-02T18:00:00Z")
    if scheduled_at <= datetime.now(timezone.utc):
        raise ValueError("Schedule time must be in the future")

    youtube = _youtube_client()
    stream = None
    request = youtube.liveStreams().list(part="id,cdn", mine=True, maxResults=50)
    while request is not None:
        result = request.execute()
        for item in result.get("items", []):
            ingestion = item.get("cdn", {}).get("ingestionInfo", {})
            if ingestion.get("streamName") == stream_key:
                stream = item
                break
        if stream:
            break
        request = youtube.liveStreams().list_next(request, result)
    if not stream:
        raise ValueError(
            "No reusable YouTube live stream matches the configured stream key. "
            "Create/select one in YouTube Studio first."
        )

    broadcast = youtube.liveBroadcasts().insert(
        part="snippet,status,contentDetails",
        body={
            "snippet": {
                "title": title,
                "description": description,
                "scheduledStartTime": scheduled_at.astimezone(timezone.utc).isoformat(),
                "categoryId": "20",
            },
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
            "contentDetails": {
                "enableAutoStart": False,
                "enableAutoStop": True,
                "recordFromStart": True,
            },
        },
    ).execute()
    broadcast_id = broadcast["id"]
    youtube.liveBroadcasts().bind(
        part="id,contentDetails", id=broadcast_id, streamId=stream["id"]
    ).execute()
    youtube.thumbnails().set(
        videoId=broadcast_id,
        media_body=MediaFileUpload(str(thumbnail), mimetype="image/png"),
    ).execute()
    return f"https://www.youtube.com/watch?v={broadcast_id}"
