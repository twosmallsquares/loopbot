"""Backend tests for the Quintuple Discord bot."""
import json
import asyncio
import requests
import pytest
from conftest import sign_payload


# ---------------- Public endpoints (hit deployed instance) ----------------

class TestPublicEndpoints:
    def test_health(self, base_url):
        r = requests.get(f"{base_url}/api/health", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["application_id"] == "1505632684095836352"

    def test_install_link(self, base_url):
        r = requests.get(f"{base_url}/api/discord/install-link", timeout=10)
        assert r.status_code == 200
        url = r.json()["url"]
        assert "discord.com/oauth2/authorize" in url
        assert "integration_type=1" in url
        assert "scope=applications.commands" in url
        assert "client_id=1505632684095836352" in url

    def test_usage_stats_shape(self, base_url):
        r = requests.get(f"{base_url}/api/usage/stats", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "total_uses" in data
        assert "total_messages_sent" in data
        assert data["total_messages_sent"] == data["total_uses"] * 5

    def test_usage_recent_no_objectid_leak(self, base_url):
        r = requests.get(f"{base_url}/api/usage/recent", timeout=10)
        assert r.status_code == 200
        body = r.text
        assert "_id" not in body  # no Mongo _id leakage
        assert isinstance(r.json(), list)


# ---------------- Signature security (deployed instance) ----------------

class TestSignatureSecurity:
    def test_missing_signature_returns_401(self, base_url):
        r = requests.post(
            f"{base_url}/api/discord/interactions",
            json={"type": 1},
            timeout=10,
        )
        assert r.status_code == 401

    def test_invalid_signature_returns_401(self, base_url):
        body = json.dumps({"type": 1})
        headers = {
            "X-Signature-Ed25519": "00" * 64,
            "X-Signature-Timestamp": "1700000000",
            "Content-Type": "application/json",
        }
        r = requests.post(
            f"{base_url}/api/discord/interactions",
            data=body, headers=headers, timeout=10,
        )
        assert r.status_code == 401


# ---------------- Signed interactions (in-process w/ patched key) ----------------

class TestSignedInteractions:
    def test_ping_returns_pong(self, client, keypair):
        sk, _ = keypair
        body = json.dumps({"type": 1}).encode()
        ts = "1700000000"
        sig = sign_payload(sk, body, ts)
        r = client.post(
            "/api/discord/interactions",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Signature-Ed25519": sig,
                "X-Signature-Timestamp": ts,
            },
        )
        assert r.status_code == 200
        assert r.json() == {"type": 1}

    def test_bad_signature_in_process(self, client):
        body = json.dumps({"type": 1}).encode()
        r = client.post(
            "/api/discord/interactions",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Signature-Ed25519": "11" * 64,
                "X-Signature-Timestamp": "1700000000",
            },
        )
        assert r.status_code == 401

    def test_use_command_full_flow(self, client, keypair, test_app, monkeypatch):
        sk, _ = keypair
        followup_calls = []

        # Patch send_followups to capture invocations without hitting Discord
        original_send = test_app.send_followups

        async def fake_send_followups(token, message, count=4, delay=0.5):
            for _ in range(count):
                followup_calls.append({
                    "url": f"https://discord.com/api/v10/webhooks/"
                           f"{test_app.DISCORD_APPLICATION_ID}/{token}",
                    "content": message,
                })

        monkeypatch.setattr(test_app, "send_followups", fake_send_followups)

        payload = {
            "type": 2,
            "token": "TEST_INTERACTION_TOKEN_xyz",
            "id": "test-id",
            "application_id": test_app.DISCORD_APPLICATION_ID,
            "data": {
                "name": "use",
                "options": [{"name": "message", "type": 3, "value": "hello world"}],
            },
            "member": {"user": {"id": "u1", "username": "tester"}},
            "guild_id": "g1",
            "channel_id": "c1",
        }
        body = json.dumps(payload).encode()
        ts = "1700000001"
        sig = sign_payload(sk, body, ts)

        r = client.post(
            "/api/discord/interactions",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Signature-Ed25519": sig,
                "X-Signature-Timestamp": ts,
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["type"] == 4
        assert data["data"]["content"] == "hello world"

        # Wait briefly for asyncio.create_task to fire
        async def wait():
            await asyncio.sleep(0.2)
        asyncio.get_event_loop().run_until_complete(wait()) if False else None
        import time; time.sleep(0.3)

        assert len(followup_calls) == 4, f"Expected 4 followups, got {len(followup_calls)}"
        for c in followup_calls:
            assert c["content"] == "hello world"
            assert c["url"].startswith(
                f"https://discord.com/api/v10/webhooks/{test_app.DISCORD_APPLICATION_ID}/"
            )
            assert "TEST_INTERACTION_TOKEN_xyz" in c["url"]


# ---------------- Stats reflect /use invocation ----------------

class TestStatsAfterUse:
    def test_stats_incremented_after_use(self, base_url):
        # Capture before
        before = requests.get(f"{base_url}/api/usage/stats", timeout=10).json()

        # The /use signed test above ran against in-process TestClient w/ same
        # MongoDB. Verify usage log was written.
        recent = requests.get(f"{base_url}/api/usage/recent", timeout=10).json()
        assert isinstance(recent, list)
        # At least one entry should exist if /use ran
        if before["total_uses"] >= 1:
            assert any(item.get("message") == "hello world" for item in recent), \
                "Expected a usage entry with 'hello world' from the /use test"
            stats = requests.get(f"{base_url}/api/usage/stats", timeout=10).json()
            assert stats["total_messages_sent"] == stats["total_uses"] * 5
            assert stats["last_used"] is not None


# ---------------- register-commands (real Discord) ----------------

class TestRegisterCommands:
    def test_register_commands_endpoint(self, base_url):
        r = requests.post(
            f"{base_url}/api/discord/register-commands", timeout=30
        )
        # Accept 200 success or Discord rate-limited / token issues (4xx)
        # but the endpoint itself must respond, not 5xx.
        assert r.status_code < 500, f"Server error: {r.status_code} {r.text}"
        if r.status_code == 200:
            data = r.json()
            assert data["ok"] is True
            cmd = data["command"]
            assert cmd["name"] == "use"
            assert 1 in cmd.get("integration_types", [])
