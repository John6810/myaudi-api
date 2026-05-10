"""Immutable OAuth state — all tokens and endpoints needed to run as an authenticated user."""

from dataclasses import dataclass, asdict, replace


@dataclass(frozen=True)
class OAuthState:
    """Snapshot of all OAuth tokens and endpoint URLs after a successful login.

    Frozen so that token refresh produces a new state via :meth:`with_refresh`
    instead of mutating in-place. (The token dicts themselves remain mutable;
    we only freeze the field references on the state object.)
    """

    bearer_token: dict
    audi_token: dict
    vw_token: dict
    mbb_oauth_token: dict
    xclient_id: str
    client_id: str
    token_endpoint: str
    authorization_server_base_url: str
    mbb_oauth_base_url: str
    language: str

    @classmethod
    def from_dict(cls, d: dict) -> "OAuthState":
        """Build from oauth.login() result or TokenStore.load() output.

        Tolerates extra keys (e.g. 'saved_at' from TokenStore) by reading
        only the fields it knows. Raises KeyError on missing required ones.
        """
        return cls(
            bearer_token=d["bearer_token"],
            audi_token=d["audi_token"],
            vw_token=d["vw_token"],
            mbb_oauth_token=d["mbb_oauth_token"],
            xclient_id=d["xclient_id"],
            client_id=d["client_id"],
            token_endpoint=d["token_endpoint"],
            authorization_server_base_url=d["authorization_server_base_url"],
            mbb_oauth_base_url=d["mbb_oauth_base_url"],
            language=d["language"],
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def with_refresh(self, refreshed: dict) -> "OAuthState":
        """Return a new state merging an oauth.refresh_tokens() result.

        Endpoint URLs and client_id are unchanged across refreshes; only the
        4 token dicts get rotated.
        """
        return replace(
            self,
            bearer_token=refreshed["bearer_token"],
            audi_token=refreshed["audi_token"],
            vw_token=refreshed["vw_token"],
            mbb_oauth_token=refreshed["mbb_oauth_token"],
        )
