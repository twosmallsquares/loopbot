# Quintuple — Discord /use Bot

## Original Problem Statement
> "Make a discord bot that sends 5 messages in a short period of time and it's an external bot that I'll have in my bot library, make the command /use MESSAGETOSEND5TIMES"

## User Choices
- External / user-installable Discord app (like `/wordle`) — works anywhere external apps are allowed, not added to a specific server.
- 5 messages with 500ms delay between each.
- `message` option is REQUIRED.
- No self-hosting — runs on Emergent infrastructure via Discord HTTP interactions.
- Bot token and public key already provided and stored in `/app/backend/.env`.

## Architecture
- **Backend**: FastAPI (`/app/backend/server.py`) handling Discord HTTP interactions.
  - `POST /api/discord/interactions` — verifies ed25519 signature (PyNaCl), handles PING + `/use`.
  - `POST /api/discord/register-commands` — registers `/use` globally with `integration_types=[0,1]`, `contexts=[0,1,2]`.
  - `GET /api/discord/install-link` — returns user-install OAuth URL.
  - `GET /api/usage/stats`, `GET /api/usage/recent` — analytics.
- **Frontend**: React dashboard (`/app/frontend/src/App.js`) — setup instructions, command registration button, live stats.
- **DB**: MongoDB `usage_logs` collection.
- **Flow**: User runs `/use message:hi` → Discord POSTs to our endpoint → we send initial response (msg #1) + schedule 4 async followups via webhook token at 500ms intervals.

## Implemented (Feb 2026)
- HTTP-interactions Discord bot with signature verification.
- `/use <message>` slash command registered globally with Discord.
- 5-message echo with 500ms delay (initial + 4 followups).
- Usage logging + dashboard with live stats (5s polling).
- Install-link generation for user installation.
- 11/11 backend tests passing.

## Setup Required by User
1. In Discord Developer Portal → app → **General Information**, set **Interactions Endpoint URL** to:
   `https://<preview-domain>/api/discord/interactions`
2. In **Installation** tab → enable **User Install** in Installation Contexts.
3. Click **Register /use globally** on dashboard (or POST `/api/discord/register-commands`).
4. Use **Add to your account** link to install.

## Backlog / Next Items
- P1: Deploy to production (one-click Emergent deploy) so endpoint stays reachable 24/7.
- P2: Rate limiting per user (prevent abuse).
- P2: Configurable delay/count via additional command options.
- P2: Restrict by user allowlist or premium tier.
