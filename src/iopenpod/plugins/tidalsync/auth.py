"""Tidal authentication wrapper using tiddl's device-code OAuth flow.

Auth data (tokens, user info) is stored in iOpenPod's own data directory so we
don't depend on tiddl's internal config format, which may change between versions.

Typical flow
------------
1. Call ``start_device_flow()``  → get a ``DeviceFlowInfo`` (URL + code to show user)
2. Poll ``poll_device_flow(device_code)`` at ``DeviceFlowInfo.poll_interval`` seconds
   until it returns a ``TidalAuthData`` (approved) or raises (expired/denied)
3. Call ``get_api()`` to get a ready-to-use ``TidalAPI`` instance at any time
4. Call ``logout()`` to clear stored tokens
"""

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_AUTH_FILE_NAME = "tidal_auth.json"


# ---------------------------------------------------------------------------
# Auth data storage
# ---------------------------------------------------------------------------

def _get_auth_path() -> Path:
    from iopenpod.infrastructure.settings_paths import default_data_dir
    return Path(default_data_dir()) / _AUTH_FILE_NAME


@dataclass
class TidalAuthData:
    access_token: str
    refresh_token: str
    user_id: int
    country_code: str
    username: str
    expires_at: float  # unix timestamp when access_token expires


def load_auth_data() -> TidalAuthData | None:
    """Return stored auth data, or ``None`` if absent or unreadable."""
    path = _get_auth_path()
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return TidalAuthData(**data)
    except Exception:
        log.exception("Failed to load Tidal auth data from %s", path)
        return None


def save_auth_data(data: TidalAuthData) -> None:
    path = _get_auth_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(asdict(data), f, indent=2)
    except Exception:
        log.exception("Failed to save Tidal auth data")


def clear_auth_data() -> None:
    path = _get_auth_path()
    try:
        if path.exists():
            path.unlink()
    except Exception:
        log.exception("Failed to clear Tidal auth data")


def is_authenticated() -> bool:
    """Return ``True`` if we have stored credentials (may be expired but refreshable)."""
    return load_auth_data() is not None


def get_username() -> str:
    """Return the stored Tidal username, or an empty string if not signed in."""
    data = load_auth_data()
    return data.username if data else ""


# ---------------------------------------------------------------------------
# TidalAPI construction
# ---------------------------------------------------------------------------

def get_api():
    """Return a ``TidalAPI`` instance using stored credentials, or ``None``.

    Handles token refresh transparently via the TidalClient's on_token_expiry
    callback if the access token has expired.
    """
    auth = load_auth_data()
    if auth is None:
        return None

    return _build_api(auth)


def _build_api(auth: TidalAuthData):
    """Construct a ``TidalAPI`` from a ``TidalAuthData`` instance."""
    from tiddl.core.api.api import TidalAPI
    from tiddl.core.api.client import TidalClient

    from iopenpod.infrastructure.settings_paths import default_data_dir

    cache_path = str(Path(default_data_dir()) / "tidal_api_cache")

    def _on_token_expiry():
        """Refresh the access token when the TidalClient gets a 401."""
        try:
            from tiddl.core.auth.api import AuthAPI
            new_resp = AuthAPI().refresh_token(auth.refresh_token)
            auth.access_token = new_resp.access_token
            auth.expires_at = time.time() + new_resp.expires_in
            save_auth_data(auth)
            log.info("Tidal access token refreshed successfully")
        except Exception:
            log.exception("Failed to refresh Tidal access token")

    client = TidalClient(
        token=auth.access_token,
        cache_name=cache_path,
        on_token_expiry=_on_token_expiry,
    )
    return TidalAPI(
        client=client,
        user_id=str(auth.user_id),
        country_code=auth.country_code,
    )


# ---------------------------------------------------------------------------
# Device-code login flow
# ---------------------------------------------------------------------------

@dataclass
class DeviceFlowInfo:
    """Information needed to show the user the Tidal login prompt."""
    device_code: str
    user_code: str
    verification_url: str   # full URL with code pre-filled — open in browser
    expires_in: int         # seconds before the code expires
    poll_interval: int      # recommended seconds between poll_device_flow() calls


def start_device_flow() -> DeviceFlowInfo:
    """Initiate a Tidal device-code OAuth flow.

    Returns a ``DeviceFlowInfo`` whose ``verification_url`` the user should
    visit to approve the connection. Then call ``poll_device_flow()`` to check
    for completion.

    Raises ``ImportError`` if tiddl is not installed.
    Raises any exception from tiddl's AuthAPI on network/API errors.
    """
    from tiddl.core.auth.api import AuthAPI

    resp = AuthAPI().get_device_auth()
    return DeviceFlowInfo(
        device_code=resp.deviceCode,
        user_code=resp.userCode,
        verification_url=resp.verificationUriComplete,
        expires_in=resp.expiresIn,
        poll_interval=resp.interval,
    )


def poll_device_flow(device_code: str) -> TidalAuthData | None:
    """Poll once for device-code auth completion.

    Returns ``TidalAuthData`` if the user has approved, ``None`` if still
    pending. Raises on expiry or hard failure.
    """
    from tiddl.core.auth.api import AuthAPI

    auth_api = AuthAPI()
    try:
        resp = auth_api.get_auth(device_code)
    except Exception as e:
        msg = str(e).lower()
        # "authorization_pending" is normal while the user hasn't approved yet
        if "authorization_pending" in msg or "pending" in msg:
            return None
        raise

    auth_data = TidalAuthData(
        access_token=resp.access_token,
        refresh_token=resp.refresh_token,
        user_id=resp.user_id,
        country_code=resp.user.countryCode,
        username=(
            resp.user.username
            or resp.user.email
            or str(resp.user_id)
        ),
        expires_at=time.time() + resp.expires_in,
    )
    save_auth_data(auth_data)
    log.info("Tidal login successful: user=%s", auth_data.username)
    return auth_data


def logout() -> None:
    """Sign out of Tidal and remove stored credentials."""
    auth = load_auth_data()
    if auth:
        try:
            from tiddl.core.auth.api import AuthAPI
            AuthAPI().logout_token(auth.access_token)
            log.info("Tidal logout API call succeeded")
        except Exception:
            log.exception("Tidal logout API call failed (clearing local data anyway)")
    clear_auth_data()
