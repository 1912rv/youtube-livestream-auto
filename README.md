# Chesscast Live

Chesscast continuously streams archived chess games with a centered board, player portraits, and instrumental background music. Before each game finishes, it loads and analyzes the next PGN and looks for public-domain or CC0 player portraits on Wikimedia Commons. The encoder and music remain active during game changes. Stockfish supplies lightweight best/good/brilliant/inaccuracy/mistake/blunder heuristics; these are not Chess.com's proprietary move ratings.

## Run

Keep `.env` private. `YOUTUBE_RTMP_URL` (or `YOUTUBE_STREAM_KEY`) is the landscape stream key; `YOUTUBE_VERTICAL_RTMP_URL` is the separate vertical stream key. Both default to YouTube's primary ingest server. Set `YOUTUBE_STREAM_URL` or `YOUTUBE_VERTICAL_STREAM_URL` only if Studio gives a different ingest server. The app sends the same game, move labels, music, and next-game preview to both outputs, rendering 1280x720 landscape and 720x1280 portrait independently.

```sh
docker build -t chesscast:local .
docker run -d --name chesscast-live --restart unless-stopped \
  --env-file .env \
  -v "$PWD/output:/output" \
  chesscast:local
```

Stop the stream with `docker stop chesscast-live`. The generated YouTube thumbnail is written to `output/chesscast-thumbnail.png` at 1280x720. The live encoder sends native 720x1280 portrait video by default, with the board, move labels, player portraits, and next-game information composed for a phone screen. Set `STREAM_LAYOUT=landscape` to send 1280x720 landscape video instead. Native portrait input is intended for YouTube's vertical live/Shorts discovery feed; Auto-crop is not a substitute for portrait output.

## YouTube Vertical Live Setup

Before starting the encoder, schedule a new stream from YouTube Studio's **Manage** page. Open its vertical layout editor and select/customize a preset, turn **Auto-start** off, and set visibility to **Public** if it should be discoverable in the Shorts feed. The encoder outputs SDR BT.709 (`yuv420p`) with no HDR metadata. Once the event is configured, start the container and wait for the preview/ingestion status in Studio, then press **Go Live** there; the publisher will not auto-start the broadcast.

The bundled portrait credits are in [assets/players/ATTRIBUTION.md](assets/players/ATTRIBUTION.md). The historical PGNs are in `games/`; clock times are not shown unless they are present in a source PGN.

At startup and before each handoff, Chesscast fetches an unseen game from public collections, prioritizing Magnus Carlsen and world champions Kasparov, Karpov, Anand, Ding, Kramnik, Fischer, Capablanca, Alekhine, Tal, Botvinnik, Lasker, and Spassky, then Morphy and Anderssen. Downloaded PGNs are cached under `output/historical-games`; only the four most recently fetched game files are retained. A small `output/historical-games.seen` file stores hashes only so games are not repeated across restarts. The default archive is [`kinairi/chess-data`](https://github.com/kinairi/chess-data), licensed GPL-3.0. Set `HISTORICAL_PGN_URLS` to a comma-separated list of HTTPS PGN URLs to override the default sources. If all archive sources are unavailable or exhausted, Chesscast falls back to bundled games.

## YouTube Metadata And Scheduling

Scheduling creates an accurate title and description (optionally refined with `OPENAI_KEY`), uploads the generated thumbnail, binds the existing reusable stream, then waits and starts publishing at the scheduled time. It requires YouTube Data API v3 enabled in a Google Cloud project and a Desktop OAuth client authorized for the channel. Download its JSON as `client_secret.json`; keep it out of source control.

Stop the immediate livestream before scheduling another broadcast on the same stream key. Run the scheduling process in the foreground so it can wait for the start time and publish the stream:

```sh
docker stop chesscast-live
docker run --rm --name chesscast-scheduled --env-file .env \
  -v "$PWD/output:/output" \
  -v "$PWD/client_secret.json:/app/client_secret.json:ro" \
  -p 127.0.0.1:8080:8080 \
  chesscast:local --schedule-at 2026-10-02T18:00:00Z --privacy private
```

On first use, authorize the channel using the URL printed in the container logs; the OAuth callback uses port 8080. Keep the process running until the scheduled time. YouTube may still require channel verification or a one-time Studio confirmation. To skip OpenAI title/description refinement, add `--no-ai-metadata`; a safe template is used instead.

## GitHub Actions

The daily workflow runs at 18:00 Asia/Kolkata (12:30 UTC), streams up to 100 complete games to both configured RTMP keys, and stops the encoders after a final game's moves. A 5.5-hour graceful runtime limit prevents the 6-hour hosted-runner timeout from interrupting a game. Add `YOUTUBE_RTMP_URL` and `YOUTUBE_VERTICAL_RTMP_URL` as repository Actions secrets. The workspace must be pushed to a GitHub repository before its scheduled workflow can run. The action does not create or transition YouTube broadcasts; schedule/configure both Studio events and enable Auto-stop there. Auto-stop is also enabled by the local OAuth scheduler.

Suggested stream title: **World Chess Champions Replayed Live | Carlsen, Kasparov & Engine Move Ratings**. The games are archived replays, not live tournament games.