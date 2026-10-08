"""Keep regression tests offline and away from the operator's token file."""

import socket

import pytest

from audi_connect.token_store import DEFAULT_TOKEN_FILE, TokenStore


@pytest.fixture(autouse=True)
def isolate_network_and_tokens(monkeypatch, tmp_path):
    connect = socket.socket.connect

    def offline_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("Live network access is disabled in tests")
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", offline_connect)
    init = TokenStore.__init__

    def isolated_store(self, filepath=DEFAULT_TOKEN_FILE):
        if filepath == DEFAULT_TOKEN_FILE:
            filepath = str(tmp_path / "default-tokens.json")
        init(self, filepath)

    monkeypatch.setattr(TokenStore, "__init__", isolated_store)
