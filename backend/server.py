from fastapi import FastAPI, APIRouter, Request, HTTPException, Header
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import asyncio
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime, timezone

import httpx
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Discord config
DISCORD_BOT_TOKEN = os.environ['DISCORD_BOT_TOKEN']
DISCORD_PUBLIC_KEY = os.environ['DISCORD_PUBLIC_KEY']
DISCORD_APPLICATION_ID = os.environ['DISCORD_APPLICATION_ID']
DISCORD_API = "https://discord.com/api/v10"

# Advertisement appended to every bot message.
AD_LINK = "https://discord.gg/hAMTVDSmd8"

# Hardcoded templates for /template subcommands.
TEMPLATE_EMBED = (
    "# AWW YOU GOT RAIDED? "
    "https://cdn.discordapp.com/attachments/1230960551853559850/1505019999528423514/"
    "aww-you-got-raided.gif?ex=6a091a99&is=6a07c919&"
    "hm=062c77f8e443aa910e21c9a6b5211e676a64b20ba9d23e3fbf731de498898bda"
)


def with_ad(content: str) -> str:
    """Append the Discord invite to every bot message."""
    return f"{content}\n{AD_LINK}"


verify_key = VerifyKey(bytes.fromhex(DISCORD_PUBLIC_KEY))

# Keep strong references to background tasks so they aren't GC'd mid-flight.
_BG_TASKS: set = set()

# Bot on/off state. Defaults to ON so the bot is 24/7 out of the box.
# The dashboard has a manual kill switch (start/stop) but no longer depends
# on browser heartbeats to stay alive.
bot_state: dict = {
    "is_running": True,
    "started_at": datetime.now(timezone.utc),
}


def bot_is_alive() -> bool:
    return bool(bot_state["is_running"])

# App + router
app = FastAPI(title="Quintuple — Discord Bot")
api_router = APIRouter(prefix="/api")


# ---------- Models ----------
class UsageLog(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    username: Optional[str] = None
    guild_id: Optional[str] = None
    channel_id: Optional[str] = None
    message: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StatsResponse(BaseModel):
    total_uses: int
    total_messages_sent: int
    last_used: Optional[str] = None


# ---------- Discord interaction types ----------
INTERACTION_PING = 1
INTERACTION_APP_COMMAND = 2

RESP_PONG = 1
RESP_CHANNEL_MESSAGE_WITH_SOURCE = 4
RESP_DEFERRED_CHANNEL_MESSAGE = 5


# ---------- Helpers ----------
async def log_usage(payload: dict, message: str) -> None:
    user = (payload.get("member") or {}).get("user") or payload.get("user") or {}
    doc = UsageLog(
        user_id=user.get("id"),
        username=user.get("username"),
        guild_id=payload.get("guild_id"),
        channel_id=payload.get("channel_id"),
        message=message,
    ).model_dump()
    doc['timestamp'] = doc['timestamp'].isoformat()
    await db.usage_logs.insert_one(doc)


async def send_followups(interaction_token: str, message: str, count: int = 5, delay: float = 0.5):
    """
    Fetch the original (ephemeral) interaction response and send `count` PUBLIC
    followup messages that reply to it via `message_reference`. Viewers who can't
    see the ephemeral original will see "Original message was deleted" while the
    invoker sees a clean reply chain.
    """
    base = f"{DISCORD_API}/webhooks/{DISCORD_APPLICATION_ID}/{interaction_token}"
    async with httpx.AsyncClient(timeout=15.0) as http:
        original_id = await _fetch_original_id(http, base)
        for _ in range(count):
            await asyncio.sleep(delay)
            body: dict = {"content": with_ad(message)}
            if original_id:
                body["message_reference"] = {
                    "type": 0,
                    "message_id": original_id,
                    "fail_if_not_exists": False,
                }
            try:
                await http.post(base, json=body)
            except Exception as e:
                logging.exception(f"Followup send failed: {e}")


async def _fetch_original_id(http: httpx.AsyncClient, base: str) -> Optional[str]:
    """Get the ID of the interaction's original (ephemeral) response, with small retry."""
    for _ in range(5):
        try:
            r = await http.get(f"{base}/messages/@original")
            if r.status_code == 200:
                oid = r.json().get("id")
                if oid:
                    return oid
        except Exception as e:
            logging.warning(f"fetch @original failed: {e}")
        await asyncio.sleep(0.15)
    return None


async def send_blame_followup(interaction_token: str, user_id: str):
    """One public followup replying to the ephemeral 'Blaming @user...' message."""
    base = f"{DISCORD_API}/webhooks/{DISCORD_APPLICATION_ID}/{interaction_token}"
    body_text = with_ad(
        f"**Thank you <@{user_id}> for choosing loop bot**\n```\n✅ Success\n```"
    )
    async with httpx.AsyncClient(timeout=15.0) as http:
        original_id = await _fetch_original_id(http, base)
        body: dict = {
            "content": body_text,
            "allowed_mentions": {"users": [user_id]},
        }
        if original_id:
            body["message_reference"] = {
                "type": 0,
                "message_id": original_id,
                "fail_if_not_exists": False,
            }
        try:
            await http.post(base, json=body)
        except Exception as e:
            logging.exception(f"Blame followup failed: {e}")


# ---------- API routes ----------
@api_router.get("/")
async def root():
    return {"service": "Quintuple Discord Bot", "status": "online"}


@api_router.get("/health")
async def health():
    return {"ok": True, "application_id": DISCORD_APPLICATION_ID}


@api_router.post("/discord/interactions")
async def discord_interactions(
    request: Request,
    x_signature_ed25519: Optional[str] = Header(default=None),
    x_signature_timestamp: Optional[str] = Header(default=None),
):
    raw_body = await request.body()

    # 1. Verify signature
    if not x_signature_ed25519 or not x_signature_timestamp:
        raise HTTPException(status_code=401, detail="Missing signature headers")
    try:
        verify_key.verify(
            (x_signature_timestamp + raw_body.decode('utf-8')).encode(),
            bytes.fromhex(x_signature_ed25519),
        )
    except BadSignatureError:
        raise HTTPException(status_code=401, detail="Invalid request signature")

    payload = await request.json()
    itype = payload.get("type")

    # 2. PING -> PONG
    if itype == INTERACTION_PING:
        return {"type": RESP_PONG}

    # 3. Application command
    if itype == INTERACTION_APP_COMMAND:
        data = payload.get("data") or {}
        name = data.get("name")
        if name == "use":
            # Gate on bot state — must be started AND a browser heartbeat within window.
            if not bot_is_alive():
                return {
                    "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                    "data": {
                        "content": "🛑 Quintuple is offline. Open the dashboard and click **Start Bot** to wake me up.",
                        "flags": 64,  # ephemeral
                    },
                }

            # Extract message option
            options = data.get("options") or []
            message = "hello"
            for opt in options:
                if opt.get("name") == "message":
                    message = str(opt.get("value", "hello"))
                    break

            # Log usage (non-blocking awaited write — fast)
            try:
                await log_usage(payload, message)
            except Exception as e:
                logging.exception(f"Log usage failed: {e}")

            # Schedule 5 public followups that REPLY to the ephemeral initial
            # response. The invoker sees the reply chain; others see
            # "Original message was deleted" with the reply content visible.
            interaction_token = payload["token"]
            task = asyncio.create_task(send_followups(interaction_token, message, count=5, delay=0.5))
            _BG_TASKS.add(task)
            task.add_done_callback(_BG_TASKS.discard)

            return {
                "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                "data": {"content": with_ad(message), "flags": 64},  # 64 = ephemeral
            }

        if name == "blame":
            if not bot_is_alive():
                return {
                    "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                    "data": {
                        "content": "🛑 Loop bot is offline. Open the dashboard and click **Start Bot** to wake me up.",
                        "flags": 64,
                    },
                }

            options = data.get("options") or []
            target_user_id: Optional[str] = None
            for opt in options:
                if opt.get("name") == "user" and opt.get("type") == 6:
                    target_user_id = str(opt.get("value"))
                    break

            if not target_user_id:
                return {
                    "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                    "data": {"content": "You must pick a user to blame.", "flags": 64},
                }

            # Log usage
            try:
                await log_usage(payload, f"/blame <@{target_user_id}>")
            except Exception as e:
                logging.exception(f"Log usage failed: {e}")

            interaction_token = payload["token"]
            task = asyncio.create_task(send_blame_followup(interaction_token, target_user_id))
            _BG_TASKS.add(task)
            task.add_done_callback(_BG_TASKS.discard)

            return {
                "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                "data": {
                    "content": with_ad(f"Blaming <@{target_user_id}>..."),
                    "flags": 64,  # ephemeral — only invoker sees
                    "allowed_mentions": {"parse": []},  # don't ping in the ephemeral
                },
            }

        if name == "template":
            if not bot_is_alive():
                return {
                    "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                    "data": {
                        "content": "🛑 Loop bot is offline. Open the dashboard and click **Start Bot** to wake me up.",
                        "flags": 64,
                    },
                }

            options = data.get("options") or []
            sub = options[0] if options else {}
            sub_name = sub.get("name")

            if sub_name == "embed":
                template_text = TEMPLATE_EMBED
            else:
                return {
                    "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                    "data": {"content": "Unknown template.", "flags": 64},
                }

            try:
                await log_usage(payload, f"/template {sub_name}")
            except Exception as e:
                logging.exception(f"Log usage failed: {e}")

            interaction_token = payload["token"]
            task = asyncio.create_task(
                send_followups(interaction_token, template_text, count=5, delay=0.5)
            )
            _BG_TASKS.add(task)
            task.add_done_callback(_BG_TASKS.discard)

            return {
                "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                "data": {"content": with_ad(template_text), "flags": 64},
            }

        return {
            "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
            "data": {"content": "Unknown command.", "flags": 64},
        }

    return JSONResponse(status_code=400, content={"error": "Unhandled interaction type"})


@api_router.post("/discord/register-commands")
async def register_commands():
    """
    Bulk-overwrite global commands: /use, /blame, /template.
    integration_types: [0]=GUILD_INSTALL, [1]=USER_INSTALL
    contexts: [0]=GUILD, [1]=BOT_DM, [2]=PRIVATE_CHANNEL
    """
    commands = [
        {
            "name": "use",
            "type": 1,
            "description": "Send a message 5 times (500ms apart).",
            "options": [
                {
                    "type": 3,
                    "name": "message",
                    "description": "The message to send 5 times.",
                    "required": True,
                }
            ],
            "integration_types": [0, 1],
            "contexts": [0, 1, 2],
        },
        {
            "name": "blame",
            "type": 1,
            "description": "Blame someone — for science.",
            "options": [
                {
                    "type": 6,  # USER
                    "name": "user",
                    "description": "The user to blame.",
                    "required": True,
                }
            ],
            "integration_types": [0, 1],
            "contexts": [0, 1, 2],
        },
        {
            "name": "template",
            "type": 1,
            "description": "Send a preset message template 5 times (like /use).",
            "options": [
                {
                    "type": 1,  # SUB_COMMAND
                    "name": "embed",
                    "description": "AWW YOU GOT RAIDED gif template.",
                }
            ],
            "integration_types": [0, 1],
            "contexts": [0, 1, 2],
        },
    ]

    url = f"{DISCORD_API}/applications/{DISCORD_APPLICATION_ID}/commands"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}

    async with httpx.AsyncClient(timeout=15.0) as http:
        # PUT = bulk overwrite (clears any commands not in this list).
        r = await http.put(url, headers=headers, json=commands)
        if r.status_code >= 300:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return {"ok": True, "commands": r.json()}


@api_router.get("/discord/install-link")
async def install_link():
    """Return Discord install URL for this user-installable app."""
    url = (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={DISCORD_APPLICATION_ID}"
        f"&integration_type=1"
        f"&scope=applications.commands"
    )
    return {"url": url}


@api_router.get("/discord/bot-install-link")
async def bot_install_link():
    """
    Install URL to ADD the bot to a server. Required for /raid because the bot
    must be a guild member with Send Messages permission to post in channels.
    permissions=2048 = SEND_MESSAGES (0x800)
    """
    url = (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={DISCORD_APPLICATION_ID}"
        f"&scope=bot+applications.commands"
        f"&permissions=2048"
    )
    return {"url": url}


# ---------- Bot on/off control (browser-driven) ----------
@api_router.post("/bot/start")
async def bot_start():
    bot_state["is_running"] = True
    bot_state["started_at"] = datetime.now(timezone.utc)
    return {"is_running": True, "alive": bot_is_alive()}


@api_router.post("/bot/stop")
async def bot_stop():
    bot_state["is_running"] = False
    bot_state["started_at"] = None
    return {"is_running": False, "alive": False}


@api_router.post("/bot/heartbeat")
async def bot_heartbeat():
    # Kept for backward compatibility with the dashboard; no longer required.
    return {"is_running": bot_state["is_running"], "alive": bot_is_alive()}


@api_router.get("/bot/status")
async def bot_status():
    started = bot_state["started_at"]
    return {
        "is_running": bot_state["is_running"],
        "alive": bot_is_alive(),
        "started_at": started.isoformat() if started else None,
        "mode": "24/7",
    }



@api_router.get("/usage/stats", response_model=StatsResponse)
async def usage_stats():
    total_uses = await db.usage_logs.count_documents({})
    last_doc = await db.usage_logs.find_one({}, {"_id": 0, "timestamp": 1}, sort=[("timestamp", -1)])
    return StatsResponse(
        total_uses=total_uses,
        total_messages_sent=total_uses * 5,
        last_used=(last_doc or {}).get("timestamp"),
    )


@api_router.get("/usage/recent", response_model=List[UsageLog])
async def usage_recent(limit: int = 20):
    docs = await db.usage_logs.find({}, {"_id": 0}).sort("timestamp", -1).to_list(limit)
    for d in docs:
        if isinstance(d.get("timestamp"), str):
            d['timestamp'] = datetime.fromisoformat(d['timestamp'])
    return docs


# Register router + middleware
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
