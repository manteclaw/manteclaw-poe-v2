"""Manteclaw Poe server bot (v2).

A FastAPI + fastapi-poe server bot that answers Poe queries with SurplusLLM.
Replaces the broken mantecclaw-poe-bot, whose provider keys died and whose
source is stuck on Alex's PC.

- Poe protocol: POST / and POST /stream (SSE), settings with the intro
  message, /help handler. GET /health for Fly health checks.
- Inference: SurplusLLM (OpenAI-compatible gateway, api.surplusllm.com).
  Model deepseek/deepseek-v4.1-flash-1017 by default (very cheap).
  Key ONLY from the SURPLUS_API_KEY env var (set as a Fly secret).
  Model from SURPLUS_MODEL env var.
- Poe access key: from POE_ACCESS_KEY env var (set as a Fly secret once
  Poe issues one for the new server URL). If unset, requests are accepted
  without key auth so the server still works for setup/smoke tests.
  If set, it is enforced (must be 32 chars).
- Provider failures are logged server-side and returned to the user as a
  clear, friendly message -- never a silent failure.

Note: fastapi-poe 0.0.83's make_app() registers POST / behind an
HTTPBearer dependency that 401s keyless requests even when no access key
is configured, so the Poe routes are defined here directly (using the
bot's own handle_query/handle_settings) instead of make_app().
"""

import json
import logging
import os

import httpx
import fastapi_poe as fp
from fastapi import FastAPI, HTTPException, Request
from sse_starlette import EventSourceResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("manteclaw-poe-v2")

VERSION = "1.0.0"  # single source of truth (fixes the old bot's 2.3.0/2.2.0 split)

INTRO_MESSAGE = (
    "Hi! I'm Manteclaw, your AI assistant. I can help with coding, writing, "
    "research, math, crypto, and more. Type /help to see what I can do!"
)

HELP_TEXT = """Hi! I'm Manteclaw — your AI assistant. Here's what I can do:

• Coding — Python, JavaScript, Solidity, debugging, code review
• Writing — drafts, rewrites, summaries, brainstorming
• Math & logic — step-by-step problem solving
• Crypto & Web3 — explanations, Base L2, gas, tokens, DeFi concepts
• Translation — 20+ languages
• Summaries — long articles, papers, and documents

Just type your question. (Note: I don't have live on-chain data, so prices
and balances may be out of date.)"""

SYSTEM_PROMPT = (
    "You are Manteclaw, a helpful AI assistant. You help with coding, "
    "writing, math, research, and crypto — especially Base L2 and Web3 "
    "topics. Be concise, direct, and practical. When you write code, give "
    "working code. If you don't know something, say so rather than "
    "guessing. You do not have live access to on-chain data, so note that "
    "prices and balances may be out of date."
)

SURPLUS_API_BASE_URL = os.environ.get(
    "SURPLUS_API_BASE_URL", "https://api.surplusllm.com"
)

# SurplusLLM is OpenAI-compatible; its chat completions live at /api/v1.
def _surplus_url() -> str:
    return f"{SURPLUS_API_BASE_URL.rstrip('/')}/api/v1/chat/completions"


# DeepSeek V4.1 Flash: very cheap (prompt ~$0.00000004/token,
# completion ~$0.00000055/token), so a $10 credit balance covers
# tens of thousands of Poe queries.
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash-1017"


def _surplus_model() -> str:
    return os.environ.get("SURPLUS_MODEL", DEFAULT_MODEL)


def _surplus_key() -> str:
    return os.environ.get("SURPLUS_API_KEY", "")


# SurplusLLM sits behind Cloudflare, which blocks the default Python
# user agent with a 403 (error 1010), so every request carries a
# browser user agent.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _surplus_client() -> httpx.AsyncClient:
    """httpx client for SurplusLLM. Passes an explicit proxy (if the env sets
    one) and disables trust_env so httpx never parses NO_PROXY -- some
    environments put bare IPv6 addresses in NO_PROXY that httpx cannot
    parse, crashing client construction with InvalidURL. On Fly.io no
    proxy env vars are set, so this connects directly."""
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    return httpx.AsyncClient(timeout=120.0, proxy=proxy_url, trust_env=False)


def _build_messages(query) -> list:
    """Map Poe ProtocolMessages to SurplusLLM chat messages, prepending the
    system prompt if the conversation has no system message."""
    role_map = {"user": "user", "bot": "assistant", "system": "system"}
    messages = []
    has_system = False
    for msg in query:
        role = role_map.get(msg.role, "user")
        if role == "system":
            has_system = True
        messages.append({"role": role, "content": msg.content})
    if not has_system:
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
    return messages


def _last_user_text(query) -> str:
    for msg in reversed(query):
        if msg.role == "user":
            return (msg.content or "").strip()
    return ""


class ManteclawBot(fp.PoeBot):
    async def get_response(self, request: fp.QueryRequest):
        # /help is answered locally, no provider call needed.
        if _last_user_text(request.query).lower() == "/help":
            yield fp.PartialResponse(text=HELP_TEXT)
            return

        api_key = _surplus_key()
        if not api_key:
            log.error("SURPLUS_API_KEY is not set; cannot answer query")
            yield fp.PartialResponse(
                text=(
                    "I'm not set up yet — my AI provider key hasn't been "
                    "configured on the server. The server owner needs to add "
                    "it, then I'll be able to answer. Please try again later."
                )
            )
            return

        messages = _build_messages(request.query)
        payload = {
            "model": _surplus_model(),
            "messages": messages,
            "stream": True,
            "temperature": 0.7,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": BROWSER_USER_AGENT,
        }
        try:
            async with _surplus_client() as client:
                async with client.stream(
                    "POST", _surplus_url(), json=payload, headers=headers
                ) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", "replace")[:500]
                        log.error(
                            "SurplusLLM API error: HTTP %s: %s",
                            resp.status_code,
                            body,
                        )
                        if resp.status_code == 401:
                            yield fp.PartialResponse(
                                text=(
                                    "My AI provider rejected the API key — it "
                                    "may be expired or invalid. The server "
                                    "owner needs to update it."
                                )
                            )
                        elif resp.status_code == 402:
                            yield fp.PartialResponse(
                                text=(
                                    "I'm out of AI credits right now — my "
                                    "provider balance is empty. The server "
                                    "owner needs to top it up."
                                )
                            )
                        elif resp.status_code == 429:
                            yield fp.PartialResponse(
                                text=(
                                    "My AI provider is rate-limiting requests "
                                    "right now — please try again in a moment."
                                )
                            )
                        else:
                            yield fp.PartialResponse(
                                text=(
                                    "I ran into an error reaching my AI "
                                    f"provider (HTTP {resp.status_code}). "
                                    "Please try again later."
                                )
                            )
                        return
                    got_any = False
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = (choices[0].get("delta") or {}).get("content") or ""
                        if delta:
                            got_any = True
                            yield fp.PartialResponse(text=delta)
                    if not got_any:
                        log.warning("SurplusLLM stream returned no content")
                        yield fp.PartialResponse(
                            text=(
                                "I got an empty response from my AI provider. "
                                "Please try again."
                            )
                        )
        except Exception as exc:
            # Broad catch on purpose: httpx.InvalidURL (a plain Exception,
            # not an HTTPError) and any other provider failure must surface
            # as a clean user-facing message, never an SSE error event.
            log.exception("SurplusLLM request failed: %s", exc)
            yield fp.PartialResponse(
                text=(
                    "I couldn't reach my AI provider just now. "
                    "Please try again in a moment."
                )
            )

    async def get_settings(self, setting: fp.SettingsRequest):
        return fp.SettingsResponse(
            allow_attachments=False,
            introduction_message=INTRO_MESSAGE,
            enable_image_comprehension=False,
        )


# --- App -----------------------------------------------------------------

bot = ManteclawBot()
bot.bot_name = "Manteclaw"

# Poe access key: Poe issues a 32-char key when the bot is created/updated
# at poe.com/create_bot?server=1. If POE_ACCESS_KEY is set and valid it is
# enforced on every Poe request. If it is set but malformed, log loudly and
# run without key auth (better than crash-looping the Fly machine).
# If unset, the server runs without key auth so setup and smoke tests work;
# set the secret once Poe issues the key.
_poe_key = os.environ.get("POE_ACCESS_KEY", "")
if _poe_key:
    if len(_poe_key) == 32:
        bot.access_key = _poe_key
    else:
        log.warning(
            "POE_ACCESS_KEY is set but is %d chars (expected 32); "
            "starting without key auth. Fix the secret.",
            len(_poe_key),
        )

app = FastAPI(title="Manteclaw AI", version=VERSION)


@app.get("/")
async def root():
    return {"name": "Manteclaw AI", "version": VERSION, "status": "running"}


@app.get("/health")
async def health():
    return {"status": "ok", "version": VERSION}


async def _poe_dispatch(request: Request):
    """Poe server-bot protocol handler (shared by POST / and POST /stream).
    Mirrors fastapi-poe's own poe_post, but enforces the access key only
    when one is configured (fastapi-poe 0.0.83's HTTPBearer dependency
    401s keyless requests even with no key set)."""
    if bot.access_key is not None:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != bot.access_key:
            raise HTTPException(
                status_code=401,
                detail="Invalid access key",
                headers={"WWW-Authenticate": "Bearer"},
            )
    body = await request.json()
    body["http_request"] = request
    req_type = body.get("type")
    ctx = fp.RequestContext(http_request=request)
    if req_type == "query":
        qr = fp.QueryRequest.parse_obj(
            {
                **body,
                "access_key": bot.access_key or "<missing>",
                "api_key": bot.access_key or "<missing>",
            }
        )
        return EventSourceResponse(bot.handle_query(qr, ctx))
    if req_type == "settings":
        return await bot.handle_settings(
            fp.SettingsRequest.parse_obj(body), ctx
        )
    if req_type == "report_feedback":
        return await bot.handle_report_feedback(
            fp.ReportFeedbackRequest.parse_obj(body), ctx
        )
    if req_type == "report_reaction":
        return await bot.handle_report_reaction(
            fp.ReportReactionRequest.parse_obj(body), ctx
        )
    if req_type == "report_error":
        return await bot.handle_report_error(
            fp.ReportErrorRequest.parse_obj(body), ctx
        )
    raise HTTPException(status_code=501, detail="Unsupported request type")


app.post("/")( _poe_dispatch)
app.post("/stream")(_poe_dispatch)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
    )
