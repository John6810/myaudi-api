"""Tests for audi_connect.token_store module."""

import json
import os
import time
import tempfile
import pytest

from audi_connect.token_store import TokenStore


@pytest.fixture
def tmp_token_file():
    """Create a temporary file path for token storage."""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)  # Start clean
    yield path
    if os.path.exists(path):
        os.remove(path)


def _make_tokens():
    return dict(
        bearer_token={"access_token": "bearer_abc", "refresh_token": "br_ref"},
        audi_token={"access_token": "audi_abc"},
        vw_token={"access_token": "vw_abc"},
        mbb_oauth_token={"refresh_token": "mbb_ref", "expires_in": 3600},
        xclient_id="xclient_123",
        client_id="client_456",
        token_endpoint="https://example.com/token",
        authorization_server_base_url="https://example.com/auth",
        mbb_oauth_base_url="https://example.com/mbb",
        language="fr",
    )


class TestTokenStore:
    def test_save_and_load(self, tmp_token_file):
        store = TokenStore(tmp_token_file)
        tokens = _make_tokens()
        store.save(**tokens)

        loaded = store.load()
        assert loaded is not None
        assert loaded["bearer_token"]["access_token"] == "bearer_abc"
        assert loaded["xclient_id"] == "xclient_123"
        assert loaded["language"] == "fr"

    def test_load_nonexistent(self, tmp_token_file):
        store = TokenStore(tmp_token_file)
        assert store.load() is None

    def test_load_expired(self, tmp_token_file):
        store = TokenStore(tmp_token_file)
        tokens = _make_tokens()
        store.save(**tokens)

        # Manually set saved_at to the past
        with open(tmp_token_file, "r") as f:
            data = json.load(f)
        data["saved_at"] = time.time() - 7200  # 2 hours ago
        with open(tmp_token_file, "w") as f:
            json.dump(data, f)

        assert store.load(max_age_seconds=3600) is None
        assert not os.path.exists(tmp_token_file)

    def test_clear(self, tmp_token_file):
        store = TokenStore(tmp_token_file)
        tokens = _make_tokens()
        store.save(**tokens)
        assert os.path.exists(tmp_token_file)

        store.clear()
        assert not os.path.exists(tmp_token_file)

    def test_clear_nonexistent(self, tmp_token_file):
        store = TokenStore(tmp_token_file)
        store.clear()  # Should not raise

    def test_load_corrupted_json(self, tmp_token_file):
        with open(tmp_token_file, "w") as f:
            f.write("not json{{{")
        store = TokenStore(tmp_token_file)
        assert store.load() is None
