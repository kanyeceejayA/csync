"""csync - sync any CSPro dictionary between a CSWeb server and a local store.

Quick start (configuration comes from .env - see .env.example):

    import csync

    csync.dictionaries()                       # what the server holds
    csync.sync_down("MY_DICT")                 # server -> local SQLite store
    csync.add_case("MY_DICT", {"ID_A": 1, "NAME": "Jane"})
    csync.add_cases("MY_DICT", rows)
    csync.remove_case("MY_DICT", key)          # tombstone, sent on next push
    csync.sync_up("MY_DICT")                   # local store -> server

Every one of these works for *any* dictionary: the .dcf describes the survey,
so nothing here is survey-specific.  For finer control (several servers, an
explicit store path, tests) build your own `Session` instead of using these
module-level shortcuts.
"""
from __future__ import annotations

from .client import CSWeb, CSWebError, bump_clock
from .config import Settings, load_env
from .dictionary import Dictionary, Item, Record, coerce
from .session import Session, normalize_row
from .store import CaseRow, Store, content_hash

__version__ = "0.1.0"
__all__ = [
    "CSWeb", "CSWebError", "bump_clock", "Settings", "load_env", "Dictionary", "Item",
    "Record", "coerce", "Session", "normalize_row", "CaseRow", "Store", "content_hash",
    "session", "use", "close", "dictionaries", "dictionary", "register_dictionary",
    "compare_dictionary", "add_case", "add_cases", "update_case", "remove_case",
    "remove_cases", "get_case", "list_cases", "counts", "sync_down", "sync_up", "sync",
    "history",
]

_session: Session | None = None


def session() -> Session:
    """The default session, built from the environment on first use."""
    global _session
    if _session is None:
        _session = Session.from_env()
    return _session


def use(new: Session) -> Session:
    """Replace the default session (handy in tests and scripts)."""
    global _session
    _session = new
    return _session


def close():
    """Close the default session's store."""
    global _session
    if _session is not None:
        _session.close()
        _session = None


# ---- shortcuts: every one of these is `session().<same name>(...)` ----------
def dictionaries():
    """Dictionaries on the server, with case counts."""
    return session().dictionaries()


def dictionary(name, refresh=False):
    """The parsed .dcf for a dictionary (from disk, the store, or the server)."""
    return session().dictionary(name, refresh=refresh)


def register_dictionary(dcf_path):
    """Upload a .dcf to the server."""
    return session().register_dictionary(dcf_path)


def compare_dictionary(name):
    """Structural differences between the local .dcf and the server's."""
    return session().compare_dictionary(name)


def add_case(name, row, **kw):
    """Add (or replace) one case in the local store."""
    return session().add_case(name, row, **kw)


def add_cases(name, rows, **kw):
    """Add many cases at once."""
    return session().add_cases(name, rows, **kw)


def update_case(name, key, changes):
    """Change some items of a case, keeping the rest."""
    return session().update_case(name, key, changes)


def remove_case(name, key, **kw):
    """Mark a case deleted (goes up as a tombstone)."""
    return session().remove_case(name, key, **kw)


def remove_cases(name, keys, **kw):
    """Mark several cases deleted."""
    return session().remove_cases(name, keys, **kw)


def get_case(name, key):
    """One case from the local store."""
    return session().get_case(name, key)


def list_cases(name, **kw):
    """Cases in the local store."""
    return session().list_cases(name, **kw)


def counts(name):
    """How many cases are here, and how many are waiting to go up."""
    return session().counts(name)


def sync_down(name, **kw):
    """Server -> local store."""
    return session().sync_down(name, **kw)


def sync_up(name, **kw):
    """Local store -> server."""
    return session().sync_up(name, **kw)


def sync(name, **kw):
    """Down then up."""
    return session().sync(name, **kw)


def history(limit=50):
    """What this tool did recently."""
    return session().store.history(limit)
