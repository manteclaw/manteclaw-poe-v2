# Manteclaw Poe Bot v2

A Poe server bot (FastAPI + fastapi-poe) that answers Poe queries with
Mistral. Replaces the broken `manteclaw-poe-bot`, whose provider keys died
and whose source is stuck on Alex's PC.

- **Live:** https://manteclaw-poe-v2.fly.dev (once deployed)
- **Poe listing:** https://poe.com/Manteclaw (point it at the new URL)

## What it does

General-purpose assistant: coding, writing, math, crypto/Base L2
questions. Speaks the Poe server-bot protocol:

- `POST /` and `POST /stream` — Poe queries (SSE `text`/`done` events)
- `POST /` with `type: "settings"` — settings (intro message, no
  attachments, no image comprehension)
- `/help` is answered locally, no provider call
- `GET /health` — liveness probe (wired into Fly as a health check)
- `GET /` — `{"name": "Manteclaw AI", "version": ..., "status": "running"}`

## Configuration (env vars — never hardcode keys)

| Var | Required | What |
|---|---|---|
| `MISTRAL_API_KEY` | yes, for real answers | Mistral API key. Set as a Fly secret: `flyctl secrets set MISTRAL_API_KEY=...`. Without it the bot starts fine but answers queries with a clear "not set up yet" message. |
| `MISTRAL_MODEL` | no (default `mistral-small-latest`) | Mistral model ID. Set in `fly.toml` or as a Fly secret. |
| `MISTRAL_API_BASE_URL` | no (default `https://api.mistral.ai`) | Override for the Mistral API base URL (testing / Mistral-compatible endpoints). |
| `POE_ACCESS_KEY` | no at first | Poe issues a 32-char key when the bot is created/updated at poe.com/create_bot?server=1. Set as a Fly secret once Poe issues it; until then requests are accepted without key auth. |
| `PORT` | no (default 8080) | Set by Fly. |

Provider failures are logged server-side and returned to the user as a
clear, friendly message — never a silent failure (that is how the old bot
broke without anyone noticing).

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
MISTRAL_API_KEY=... .venv/bin/python server.py
# or: PORT=18090 .venv/bin/uvicorn server:app --host 0.0.0.0 --port 18090
curl http://localhost:8080/health
```

## Deploy

Deploys go through GitHub Actions (`.github/workflows/deploy.yml`) to
Fly.io (`flyctl deploy --remote-only`). The workflow needs a
`FLY_API_TOKEN` repo secret (a Fly.io org deploy token for the `personal`
org). The Dockerfile lists every file the image ships explicitly.

## Poe setup

1. Deploy, then confirm `GET /health` returns 200.
2. Set `MISTRAL_API_KEY` as a Fly secret, then test a real query.
3. On Poe, update the bot's server URL to
   `https://manteclaw-poe-v2.fly.dev` (Poe setup only needs a server URL).
4. Poe issues an access key — set it as the `POE_ACCESS_KEY` Fly secret.
