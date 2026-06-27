#!/usr/bin/env python3
"""Plaud API access + auth, shared by pull-plaud.py and check-plaud-completeness.py.

Two backends, auto-selected by load_plaud_auth():

  oauth  (preferred): official developer API at platform.plaud.ai/developer/api.
         Token comes from `plaud login` (the official CLI), stored at
         ~/.plaud/tokens.json and auto-refreshed. We nudge a refresh via `plaud me`
         when the access token is near expiry.

  legacy (fallback)  : consumer API at api-*.plaud.ai, using a hand-copied
         web.plaud.ai `tokenstr` in _scripts/.env (PLAUD_TOKEN). This is the old
         path; kept only as a safety net during the OAuth parity window. Remove
         PLAUD_TOKEN from .env once OAuth is proven.

Both backends are normalized to one item shape so downstream code is backend-agnostic:
    {id, filename, start_time (epoch ms, UTC), duration (ms), is_trash, is_trans}
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"
OAUTH_TOKEN_FILE = Path.home() / ".plaud" / "tokens.json"

DEV_API_BASE = "https://platform.plaud.ai/developer/api"
LEGACY_DEFAULT_DOMAIN = "https://api-use1.plaud.ai"
LEGACY_VALID_DOMAINS = {"api-euc1.plaud.ai", "api-use1.plaud.ai", "api.plaud.ai", "api-apse1.plaud.ai"}

# Refresh the OAuth token if it expires within this window (ms).
_REFRESH_MARGIN_MS = 5 * 60 * 1000


class PlaudAuth:
    """Resolved Plaud credentials: which backend, the bearer token, the base URL."""

    def __init__(self, mode, token, base):
        self.mode = mode      # "oauth" | "legacy"
        self.token = token
        self.base = base

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}


def _now_ms():
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def _load_oauth_auth():
    """Return a PlaudAuth for the official developer API, or None if OAuth isn't usable.

    Nudges `plaud me` to refresh when the access token is near/after expiry. If it's
    still expired after the nudge (e.g. the refresh token died, or the CLI isn't on
    PATH), returns None so the caller can fall back to the legacy token.
    """
    if not OAUTH_TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(OAUTH_TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None

    token = data.get("access_token")
    expires_at = data.get("expires_at", 0)  # epoch ms
    if not token:
        return None

    if expires_at - _now_ms() < _REFRESH_MARGIN_MS:
        exe = shutil.which("plaud") or shutil.which("plaud.cmd")
        if exe:
            try:
                subprocess.run([exe, "me"], capture_output=True, timeout=30)
                data = json.loads(OAUTH_TOKEN_FILE.read_text(encoding="utf-8"))
                token = data.get("access_token")
                expires_at = data.get("expires_at", 0)
            except Exception:
                pass
        if expires_at - _now_ms() < 0:  # still expired -> OAuth unusable
            return None

    return PlaudAuth("oauth", token, DEV_API_BASE)


def _load_legacy_auth():
    """Return a PlaudAuth for the legacy consumer API from .env, or None if unset."""
    try:
        from dotenv import load_dotenv
        if ENV_FILE.exists():
            load_dotenv(ENV_FILE)
    except ImportError:
        pass

    token = os.environ.get("PLAUD_TOKEN", "").strip()
    if not token:
        return None

    domain = os.environ.get("PLAUD_API_DOMAIN", LEGACY_DEFAULT_DOMAIN).strip().rstrip("/")
    host = urlparse(domain).hostname or ""
    if not (host.endswith(".plaud.ai") and host in LEGACY_VALID_DOMAINS):
        print(f"ERROR: API domain '{domain}' is not a known Plaud domain.", file=sys.stderr)
        print(f"Allowed: {', '.join(sorted(LEGACY_VALID_DOMAINS))}", file=sys.stderr)
        sys.exit(1)
    return PlaudAuth("legacy", token, domain)


def load_plaud_auth():
    """Resolve Plaud credentials. OAuth first, legacy .env fallback, else None."""
    return _load_oauth_auth() or _load_legacy_auth()


def _iso_to_epoch_ms(value):
    """Developer API timestamps are UTC-naive ISO strings -> epoch ms (UTC)."""
    if not value:
        return 0
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


class PlaudClient:
    """Backend-agnostic Plaud client. Talks to the developer API (oauth) or the
    legacy consumer API (legacy) and returns one normalized item shape either way."""

    def __init__(self, auth):
        self.auth = auth
        self.session = requests.Session()
        self.session.headers.update(auth.headers)

    def _get(self, path, params=None, _attempts=3):
        url = f"{self.auth.base}{path}"
        for i in range(_attempts):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                resp.raise_for_status()
                return resp.json()
            except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status is not None and status < 500:
                    raise  # client error (4xx), don't retry
                if i == _attempts - 1:
                    raise  # transient 5xx / network error, retries exhausted
                time.sleep(2 ** i)  # backoff: 1s, 2s

    # --- listing -----------------------------------------------------------
    def list_all_files(self):
        """Return all recordings as normalized item dicts (see module docstring)."""
        if self.auth.mode == "oauth":
            return self._list_dev()
        return self._list_legacy()

    def _list_dev(self):
        out = []
        page, size = 1, 100  # developer API caps page_size at 100
        while True:
            data = self._get("/open/third-party/files/", params={"page": page, "page_size": size})
            items = data.get("data", []) if isinstance(data, dict) else []
            for it in items:
                out.append({
                    "id": it.get("id"),
                    "filename": it.get("name", "Untitled"),
                    "start_time": _iso_to_epoch_ms(it.get("start_at") or it.get("created_at")),
                    "duration": it.get("duration", 0),  # already ms
                    # Developer API doesn't surface these at list level; the build step
                    # skips recordings whose detail has no transcript yet.
                    "is_trash": False,
                    "is_trans": True,
                })
            if not items or len(items) < size:
                break
            page += 1
        return out

    def _list_legacy(self):
        out = []
        skip, limit = 0, 100
        while True:
            data = self._get("/file/simple/web", params={"skip": skip, "limit": limit})
            files = data.get("data_file_list", [])
            total = data.get("data_file_total", 0)
            out.extend(files)
            skip += limit
            if skip >= total or not files:
                break
        return out

    # --- detail ------------------------------------------------------------
    def get_file_detail(self, file_id):
        """Full recording detail. oauth: transcript/notes/audio are inline.
        legacy: content_list with S3 data_link references."""
        if self.auth.mode == "oauth":
            return self._get(f"/open/third-party/files/{file_id}")
        return self._get(f"/file/detail/{file_id}").get("data", {})

    # --- audio -------------------------------------------------------------
    def download_audio(self, file_id, dest_path, detail=None):
        """Download the recording's audio to dest_path.
        oauth: GET the detail's presigned_url. legacy: GET /file/download/{id}."""
        if self.auth.mode == "oauth":
            url = (detail or self.get_file_detail(file_id)).get("presigned_url")
            if not url:
                raise ValueError("no presigned_url in detail")
            resp = requests.get(url, stream=True, timeout=120)
        else:
            resp = self.session.get(f"{self.auth.base}/file/download/{file_id}", stream=True, timeout=120)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
