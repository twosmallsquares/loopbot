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
ADMIN_PASSWORD = os.environ['ADMIN_PASSWORD']

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
INTERACTION_MESSAGE_COMPONENT = 3

RESP_PONG = 1
RESP_CHANNEL_MESSAGE_WITH_SOURCE = 4
RESP_DEFERRED_CHANNEL_MESSAGE = 5
RESP_DEFERRED_UPDATE_MESSAGE = 6
RESP_UPDATE_MESSAGE = 7

MENU_MAX = 45


def public_content(message: str, ping: bool) -> tuple[str, dict]:
    """Build (content, allowed_mentions) for a PUBLIC bot message.
    Appends the invite. Prefixes @everyone when ping=True.
    """
    body = with_ad(message)
    if ping:
        body = "@everyone\n" + body
        return body, {"parse": ["everyone"]}
    return body, {"parse": []}


def get_option(options: list, name: str, default=None):
    for opt in options or []:
        if opt.get("name") == name:
            return opt.get("value", default)
    return default


# ---------- Helpers ----------
async def log_usage(payload: dict, message: str, command: str = "") -> None:
    user = (payload.get("member") or {}).get("user") or payload.get("user") or {}
    doc = UsageLog(
        user_id=user.get("id"),
        username=user.get("username"),
        guild_id=payload.get("guild_id"),
        channel_id=payload.get("channel_id"),
        message=message,
    ).model_dump()
    doc['timestamp'] = doc['timestamp'].isoformat()
    doc['command'] = command
    await db.usage_logs.insert_one(doc)


def log_usage_bg(payload: dict, message: str, command: str = "") -> None:
    """Fire-and-forget version — keeps the interaction response under Discord's 3s deadline."""
    t = asyncio.create_task(log_usage(payload, message, command))
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)


# ---------- Blacklist (in-memory cache to keep interaction handler fast) ----------
_blacklist_cache: set = set()


async def _reload_blacklist_cache() -> None:
    global _blacklist_cache
    try:
        docs = await db.blacklist.find({}, {"_id": 0, "user_id": 1}).to_list(10000)
        _blacklist_cache = {d["user_id"] for d in docs if d.get("user_id")}
    except Exception as e:
        logging.exception(f"blacklist cache reload failed: {e}")


def is_blacklisted(user_id: Optional[str]) -> bool:
    """O(1) in-memory check — NO DB roundtrip on the interaction hot path."""
    if not user_id:
        return False
    return user_id in _blacklist_cache


def invoker_id(payload: dict) -> Optional[str]:
    user = (payload.get("member") or {}).get("user") or payload.get("user") or {}
    return user.get("id")


async def send_followups(
    interaction_token: str,
    message: str,
    count: int = 5,
    delay: float = 0.5,
    ping: bool = False,
):
    """
    Fetch the original (ephemeral) interaction response and send `count` PUBLIC
    followup messages that reply to it via `message_reference`. Viewers who can't
    see the ephemeral original will see "Original message was deleted" while the
    invoker sees a clean reply chain.
    """
    base = f"{DISCORD_API}/webhooks/{DISCORD_APPLICATION_ID}/{interaction_token}"
    content, mentions = public_content(message, ping)
    async with httpx.AsyncClient(timeout=15.0) as http:
        original_id = await _fetch_original_id(http, base)
        for _ in range(count):
            await asyncio.sleep(delay)
            body: dict = {"content": content, "allowed_mentions": mentions}
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


async def send_blame_followup(interaction_token: str, user_id: str, ping: bool = False):
    """One public followup replying to the ephemeral 'Blaming @user...' message."""
    base = f"{DISCORD_API}/webhooks/{DISCORD_APPLICATION_ID}/{interaction_token}"
    raw = f"**Thank you <@{user_id}> for choosing loop bot**\n```\n✅ Success\n```"
    if ping:
        raw = "@everyone\n" + raw
    body_text = with_ad(raw)
    mentions: dict = {"users": [user_id]}
    if ping:
        mentions["parse"] = ["everyone"]
    async with httpx.AsyncClient(timeout=15.0) as http:
        original_id = await _fetch_original_id(http, base)
        body: dict = {
            "content": body_text,
            "allowed_mentions": mentions,
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

        # Blacklist check (in-memory, instant)
        if is_blacklisted(invoker_id(payload)):
            return {
                "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                "data": {
                    "content": "🚫 You are blacklisted from using this bot.",
                    "flags": 64,
                },
            }

        if name == "use":
            if not bot_is_alive():
                return {
                    "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                    "data": {
                        "content": "🛑 Quintuple is offline. Open the dashboard and click **Start Bot** to wake me up.",
                        "flags": 64,
                    },
                }

            options = data.get("options") or []
            message = str(get_option(options, "message", "hello"))
            ping = bool(get_option(options, "ping", False))

            try:
                log_usage_bg(payload, message, "use")
            except Exception as e:
                logging.exception(f"Log usage failed: {e}")

            interaction_token = payload["token"]
            task = asyncio.create_task(
                send_followups(interaction_token, message, count=5, delay=0.5, ping=ping)
            )
            _BG_TASKS.add(task)
            task.add_done_callback(_BG_TASKS.discard)

            return {
                "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                "data": {
                    "content": with_ad(message),
                    "flags": 64,
                    "allowed_mentions": {"parse": []},
                },
            }

        if name == "say":
            if not bot_is_alive():
                return {
                    "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                    "data": {"content": "🛑 Bot is offline.", "flags": 64},
                }

            options = data.get("options") or []
            message = str(get_option(options, "message", "hello"))
            ping = bool(get_option(options, "ping", False))

            try:
                log_usage_bg(payload, message, "say")
            except Exception as e:
                logging.exception(f"Log usage failed: {e}")

            interaction_token = payload["token"]
            task = asyncio.create_task(
                send_followups(interaction_token, message, count=1, delay=0.0, ping=ping)
            )
            _BG_TASKS.add(task)
            task.add_done_callback(_BG_TASKS.discard)

            return {
                "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                "data": {
                    "content": with_ad(message),
                    "flags": 64,
                    "allowed_mentions": {"parse": []},
                },
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
            ping = bool(get_option(options, "ping", False))

            if not target_user_id:
                return {
                    "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                    "data": {"content": "You must pick a user to blame.", "flags": 64},
                }

            try:
                log_usage_bg(payload, f"/blame <@{target_user_id}>", "blame")
            except Exception as e:
                logging.exception(f"Log usage failed: {e}")

            interaction_token = payload["token"]
            task = asyncio.create_task(
                send_blame_followup(interaction_token, target_user_id, ping=ping)
            )
            _BG_TASKS.add(task)
            task.add_done_callback(_BG_TASKS.discard)

            return {
                "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                "data": {
                    "content": with_ad(f"Blaming <@{target_user_id}>..."),
                    "flags": 64,
                    "allowed_mentions": {"parse": []},
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
            sub_options = sub.get("options") or []
            ping = bool(get_option(sub_options, "ping", False))

            if sub_name == "embed":
                template_text = TEMPLATE_EMBED
            else:
                return {
                    "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                    "data": {"content": "Unknown template.", "flags": 64},
                }

            try:
                log_usage_bg(payload, f"/template {sub_name}", "template")
            except Exception as e:
                logging.exception(f"Log usage failed: {e}")

            interaction_token = payload["token"]
            task = asyncio.create_task(
                send_followups(interaction_token, template_text, count=5, delay=0.5, ping=ping)
            )
            _BG_TASKS.add(task)
            task.add_done_callback(_BG_TASKS.discard)

            return {
                "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                "data": {
                    "content": with_ad(template_text),
                    "flags": 64,
                    "allowed_mentions": {"parse": []},
                },
            }

        if name == "menu":
            if not bot_is_alive():
                return {
                    "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                    "data": {
                        "content": "🛑 Loop bot is offline.",
                        "flags": 64,
                    },
                }

            options = data.get("options") or []
            message = str(get_option(options, "message", "")).strip()
            ping = bool(get_option(options, "ping", False))
            if not message:
                return {
                    "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                    "data": {"content": "Provide a message.", "flags": 64},
                }

            user = (payload.get("member") or {}).get("user") or payload.get("user") or {}
            state_id = str(uuid.uuid4())
            menu_doc = {
                "id": state_id,
                "user_id": user.get("id"),
                "message": message,
                "ping": ping,
                "count": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            # Fire-and-forget the insert; it'll complete long before the user clicks Add.
            t = asyncio.create_task(db.menu_states.insert_one(menu_doc))
            _BG_TASKS.add(t)
            t.add_done_callback(_BG_TASKS.discard)

            try:
                log_usage_bg(payload, f"/menu {message}", "menu")
            except Exception as e:
                logging.exception(f"Log usage failed: {e}")

            return {
                "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                "data": {
                    "content": _menu_display(message, ping, 0),
                    "flags": 64,
                    "allowed_mentions": {"parse": []},
                    "components": _menu_components(state_id),
                },
            }

        return {
            "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
            "data": {"content": "Unknown command.", "flags": 64},
        }

    # 4. Message component (button click)
    if itype == INTERACTION_MESSAGE_COMPONENT:
        if is_blacklisted(invoker_id(payload)):
            return {
                "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                "data": {
                    "content": "🚫 You are blacklisted from using this bot.",
                    "flags": 64,
                },
            }
        return await _handle_component(payload)

    return JSONResponse(status_code=400, content={"error": "Unhandled interaction type"})


def _menu_display(message: str, ping: bool, count: int) -> str:
    ping_str = "Yes (will @everyone)" if ping else "No"
    msg_preview = message if len(message) <= 200 else message[:200] + "…"
    return (
        f"🎯 **Menu queued**\n"
        f"Message: `{msg_preview}`\n"
        f"Ping: **{ping_str}**\n"
        f"Count: **{count} / {MENU_MAX}**\n\n"
        f"Click **Add** to queue more. Click **Release** to fire."
    )


def _menu_components(state_id: str) -> list:
    return [
        {
            "type": 1,  # ACTION_ROW
            "components": [
                {
                    "type": 2,  # BUTTON
                    "style": 1,  # PRIMARY (blue)
                    "label": "Add",
                    "custom_id": f"menu:{state_id}:add",
                },
                {
                    "type": 2,
                    "style": 2,  # SECONDARY (grey)
                    "label": "MAX",
                    "custom_id": f"menu:{state_id}:max",
                },
                {
                    "type": 2,
                    "style": 4,  # DANGER (red)
                    "label": "Release",
                    "custom_id": f"menu:{state_id}:release",
                },
            ],
        }
    ]


async def _handle_component(payload: dict) -> dict:
    data = payload.get("data") or {}
    custom_id = data.get("custom_id", "")
    parts = custom_id.split(":")
    if len(parts) != 3 or parts[0] != "menu":
        return {
            "type": RESP_UPDATE_MESSAGE,
            "data": {"content": "Unknown button.", "components": []},
        }
    _, state_id, action = parts

    user = (payload.get("member") or {}).get("user") or payload.get("user") or {}
    state = await db.menu_states.find_one({"id": state_id}, {"_id": 0})
    if not state:
        return {
            "type": RESP_UPDATE_MESSAGE,
            "data": {"content": "❌ Menu expired or not found.", "components": []},
        }
    if state.get("user_id") and user.get("id") and state["user_id"] != user.get("id"):
        return {
            "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
            "data": {"content": "This menu isn't yours.", "flags": 64},
        }

    if action == "add":
        new_count = min(int(state.get("count", 0)) + 1, MENU_MAX)
        await db.menu_states.update_one({"id": state_id}, {"$set": {"count": new_count}})
        return {
            "type": RESP_UPDATE_MESSAGE,
            "data": {
                "content": _menu_display(state["message"], state["ping"], new_count),
                "components": _menu_components(state_id),
                "allowed_mentions": {"parse": []},
            },
        }

    if action == "max":
        await db.menu_states.update_one({"id": state_id}, {"$set": {"count": MENU_MAX}})
        return {
            "type": RESP_UPDATE_MESSAGE,
            "data": {
                "content": _menu_display(state["message"], state["ping"], MENU_MAX),
                "components": _menu_components(state_id),
                "allowed_mentions": {"parse": []},
            },
        }

    if action == "release":
        count = int(state.get("count", 0))
        if count <= 0:
            return {
                "type": RESP_CHANNEL_MESSAGE_WITH_SOURCE,
                "data": {"content": "Queue is empty. Click **Add** first.", "flags": 64},
            }
        # Fire background task to send `count` followups
        interaction_token = payload["token"]
        message = state["message"]
        ping = bool(state.get("ping", False))
        task = asyncio.create_task(
            send_followups(interaction_token, message, count=count, delay=0.5, ping=ping)
        )
        _BG_TASKS.add(task)
        task.add_done_callback(_BG_TASKS.discard)
        # Clean up state
        await db.menu_states.delete_one({"id": state_id})
        return {
            "type": RESP_UPDATE_MESSAGE,
            "data": {
                "content": f"✅ Released **{count}** message(s).\n{AD_LINK}",
                "components": [],
                "allowed_mentions": {"parse": []},
            },
        }

    return {
        "type": RESP_UPDATE_MESSAGE,
        "data": {"content": "Unknown action.", "components": []},
    }


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
                },
                {
                    "type": 5,  # BOOLEAN
                    "name": "ping",
                    "description": "Ping @everyone with each message? Default no.",
                    "required": False,
                },
            ],
            "integration_types": [0, 1],
            "contexts": [0, 1, 2],
        },
        {
            "name": "say",
            "type": 1,
            "description": "Send a single message (like /use but once).",
            "options": [
                {
                    "type": 3,
                    "name": "message",
                    "description": "The message to send.",
                    "required": True,
                },
                {
                    "type": 5,
                    "name": "ping",
                    "description": "Ping @everyone? Default no.",
                    "required": False,
                },
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
                },
                {
                    "type": 5,
                    "name": "ping",
                    "description": "Ping @everyone too? Default no.",
                    "required": False,
                },
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
                    "options": [
                        {
                            "type": 5,
                            "name": "ping",
                            "description": "Ping @everyone with each message? Default no.",
                            "required": False,
                        }
                    ],
                }
            ],
            "integration_types": [0, 1],
            "contexts": [0, 1, 2],
        },
        {
            "name": "menu",
            "type": 1,
            "description": "Queue a message with Add/Release buttons (max 45).",
            "options": [
                {
                    "type": 3,
                    "name": "message",
                    "description": "The message to queue.",
                    "required": True,
                },
                {
                    "type": 5,
                    "name": "ping",
                    "description": "Ping @everyone on release? Default no.",
                    "required": False,
                },
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


# ---------- Admin console ----------
class AdminAuth(BaseModel):
    password: str


class BlacklistEntry(BaseModel):
    user_id: str
    username: Optional[str] = None
    reason: Optional[str] = None


def _check_admin(password: Optional[str]) -> None:
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Wrong password")


@api_router.post("/admin/verify")
async def admin_verify(body: AdminAuth):
    _check_admin(body.password)
    return {"ok": True}


@api_router.get("/admin/logs")
async def admin_logs(
    password: str,
    limit: int = 200,
    user_id: Optional[str] = None,
    guild_id: Optional[str] = None,
):
    _check_admin(password)
    query: dict = {}
    if user_id:
        query["user_id"] = user_id
    if guild_id:
        query["guild_id"] = guild_id
    docs = (
        await db.usage_logs.find(query, {"_id": 0})
        .sort("timestamp", -1)
        .to_list(limit)
    )
    return {"logs": docs, "count": len(docs)}


@api_router.get("/admin/blacklist")
async def admin_blacklist(password: str):
    _check_admin(password)
    docs = await db.blacklist.find({}, {"_id": 0}).sort("blacklisted_at", -1).to_list(500)
    return {"blacklist": docs}


@api_router.post("/admin/blacklist")
async def admin_blacklist_add(entry: BlacklistEntry, password: str):
    _check_admin(password)
    if not entry.user_id:
        raise HTTPException(status_code=400, detail="user_id required")
    doc = {
        "user_id": entry.user_id,
        "username": entry.username,
        "reason": entry.reason,
        "blacklisted_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.blacklist.update_one(
        {"user_id": entry.user_id},
        {"$set": doc},
        upsert=True,
    )
    _blacklist_cache.add(entry.user_id)
    return {"ok": True, "entry": doc}


@api_router.delete("/admin/blacklist/{user_id}")
async def admin_blacklist_remove(user_id: str, password: str):
    _check_admin(password)
    result = await db.blacklist.delete_one({"user_id": user_id})
    _blacklist_cache.discard(user_id)
    return {"ok": True, "deleted": result.deleted_count}


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


@app.on_event("startup")
async def _on_startup():
    # Force the bot ON every boot — never silently offline.
    bot_state["is_running"] = True
    bot_state["started_at"] = datetime.now(timezone.utc)
    # Warm the blacklist cache so the interaction handler has no DB roundtrip.
    await _reload_blacklist_cache()
    logging.info(f"Bot ON, blacklist cached: {len(_blacklist_cache)} entries")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
