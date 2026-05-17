import os
import sys
import pytest
from pathlib import Path

# Make backend importable
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(scope="session")
def base_url():
    from dotenv import dotenv_values
    env = dotenv_values(BACKEND_DIR.parent / "frontend" / ".env")
    url = env.get("REACT_APP_BACKEND_URL")
    assert url, "REACT_APP_BACKEND_URL missing"
    return url.rstrip("/")


@pytest.fixture(scope="session")
def keypair():
    """Generate ed25519 keypair for signature testing."""
    from nacl.signing import SigningKey
    sk = SigningKey.generate()
    vk = sk.verify_key
    return sk, vk


@pytest.fixture(scope="session")
def test_app(keypair):
    """Import server module and swap verify_key with our generated one."""
    _, vk = keypair
    import server
    server.verify_key = vk  # monkey-patch module-level verify_key
    return server


@pytest.fixture()
def client(test_app):
    from fastapi.testclient import TestClient
    return TestClient(test_app.app)


def sign_payload(signing_key, body: bytes, timestamp: str) -> str:
    sig = signing_key.sign(timestamp.encode() + body).signature
    return sig.hex()
