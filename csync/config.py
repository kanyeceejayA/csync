"""Configuration.

Everything is read from the environment, optionally seeded from a `.env` file
in the current directory (or wherever CSYNC_ENV points).  No other module in
this package touches os.environ, so a script or a test can build its own
Settings object and never go near the environment at all.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Name of every setting, with its default.  Keep in sync with .env.example.
DEFAULTS = {
    "CSWEB_URL": "",                     # https://server/csweb/api  ("/api" added if missing)
    "CSWEB_USER": "",
    "CSWEB_PASSWORD": "",
    "CSWEB_DEVICE": "csync",             # device id written into vector clocks
    "CSWEB_VERIFY_TLS": "1",             # 0 for a self-signed dev server
    "CSWEB_TIMEOUT": "300",              # seconds per request
    "CSYNC_STORE": "data/csync.db",      # local SQLite mirror
    "CSYNC_DICTS": "dicts",              # folder searched for local .dcf files
    "CSYNC_BATCH": "200",                # cases per POST (CSWeb chokes on huge bodies)
    "CSYNC_WEB_HOST": "127.0.0.1",
    "CSYNC_WEB_PORT": "8800",
}


def load_env(path: str | os.PathLike | None = None) -> dict:
    """Read a `.env` file into os.environ.  Existing variables always win.

    Deliberately tiny: `KEY=value`, `#` comments, optional quotes.  Returns the
    values it found (whether or not they were applied).
    """
    path = Path(path or os.environ.get("CSYNC_ENV") or ".env")
    found: dict[str, str] = {}
    if not path.is_file():
        return found
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] in "\"'" and value[-1:] == value[:1] and len(value) > 1:
            value = value[1:-1]                     # quoted: keep it verbatim
        elif "#" in value:
            value = value.split("#", 1)[0].strip()  # unquoted: trailing comment
        found[key] = value
        os.environ.setdefault(key, value)
    return found


@dataclass
class Settings:
    """Resolved configuration.  Build with `Settings.from_env()`."""

    url: str = ""
    user: str = ""
    password: str = ""
    device: str = "csync"
    verify_tls: bool = True
    timeout: int = 300
    store: str = "data/csync.db"
    dict_dir: str = "dicts"
    batch: int = 200
    web_host: str = "127.0.0.1"
    web_port: int = 8800

    @classmethod
    def from_env(cls, env_file: str | os.PathLike | None = None) -> "Settings":
        load_env(env_file)
        get = lambda k: os.environ.get(k, DEFAULTS[k])  # noqa: E731
        return cls(
            url=get("CSWEB_URL").strip(),
            user=get("CSWEB_USER"),
            password=get("CSWEB_PASSWORD"),
            device=get("CSWEB_DEVICE") or "csync",
            verify_tls=get("CSWEB_VERIFY_TLS") not in ("0", "false", "False", "no"),
            timeout=int(get("CSWEB_TIMEOUT")),
            store=get("CSYNC_STORE"),
            dict_dir=get("CSYNC_DICTS"),
            batch=int(get("CSYNC_BATCH")),
            web_host=get("CSYNC_WEB_HOST"),
            web_port=int(get("CSYNC_WEB_PORT")),
        )

    @property
    def configured(self) -> bool:
        """True when there is enough here to talk to a server."""
        return bool(self.url and self.user)
