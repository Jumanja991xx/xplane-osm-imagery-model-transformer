# xpsb – Backend

Python-Backend (FastAPI) des X-Plane AI-Scenery Builders. Läuft als Sidecar-Prozess
der Desktop-App und lauscht nur auf `127.0.0.1`.

```bash
uv sync
# Entwicklung: fester Port, festes Token
uv run xpsb-backend --port 8765 --token dev --dev-cors
uv run pytest
```

Beim Start gibt das Backend genau eine Zeile `XPSB_READY {"port": <port>}` auf
stdout aus. Das Token kommt aus `--token` oder der Umgebungsvariable `XPSB_TOKEN`.
