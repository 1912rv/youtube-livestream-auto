FROM python:3.12-slim

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    STREAM_LAYOUT=vertical \
    HISTORICAL_GAME_DIR=/games \
    CHESSCAST_PLAYER_DIR=/app/assets/players \
    CHESSCAST_BACKGROUND=/app/assets/chess_live_thumbnail.png

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg stockfish libcairo2 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY games /games
COPY assets ./assets
RUN mkdir -p /output

CMD ["python", "-m", "chesscast.main"]