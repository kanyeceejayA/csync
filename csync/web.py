"""A very small web front end (optional - everything works without it).

    python -m csync web        # http://127.0.0.1:8800

It is a thin skin over the same functions the library exposes: pick a
dictionary, see what is in the local store, add or delete a case, and sync
down or up.  It also serves the bundled CSWeb API spec at /api-docs.

Flask is the only dependency in this repo, and only this file needs it.
"""
from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

from .client import CSWebError
from .config import Settings
from .session import Session

DOCS = Path(__file__).resolve().parent.parent / "docs"


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env()
    app = Flask(__name__)

    def session() -> Session:
        """A fresh session per request - SQLite connections are not shared."""
        return Session(settings)

    def fail(e):
        return jsonify({"error": str(e)}), 502 if isinstance(e, CSWebError) else 400

    # ---- pages --------------------------------------------------------------
    @app.get("/")
    def index():
        return render_template("index.html", server=settings.url or "(not configured)")

    @app.get("/api-docs")
    def api_docs():
        """Swagger UI for the bundled CSWeb sync API spec."""
        return send_from_directory(DOCS, "api.html")

    @app.get("/csweb-swagger.json")
    def swagger():
        return send_from_directory(DOCS, "csweb-swagger.json")

    # ---- data ---------------------------------------------------------------
    @app.get("/ui/dictionaries")
    def dictionaries():
        with session() as s:
            local = {d["name"]: d for d in s.store.dictionaries()}
            try:
                remote, error = s.client.dictionaries(), None
            except CSWebError as e:
                remote, error = [], str(e)
            names = {d["name"] for d in remote} | set(local)
            out = []
            for name in sorted(names):
                server = next((d for d in remote if d["name"] == name), {})
                counts = s.store.counts(name)
                out.append({"name": name, "label": server.get("label", ""),
                            "server_cases": server.get("caseCount"),
                            "known_locally": name in local, **counts})
            return jsonify({"dictionaries": out, "error": error})

    @app.get("/ui/<name>/shape")
    def shape(name):
        """Item metadata, so the browser can build a form and a table."""
        with session() as s:
            try:
                d = s.dictionary(name)
            except (CSWebError, ValueError, KeyError) as e:
                return fail(e)
            item = lambda i: {"name": i.name, "label": i.label, "type": i.type,  # noqa: E731
                              "length": i.length, "record": i.record,
                              "values": i.values, "id": i.record is None}
            return jsonify({
                "name": d.name, "label": d.label,
                "ids": [item(i) for i in d.ids],
                "records": [{"name": r.name, "label": r.label, "repeating": r.repeating,
                             "max": r.max, "items": [item(i) for i in r.items]}
                            for r in d.records],
            })

    @app.get("/ui/<name>/cases")
    def cases(name):
        with session() as s:
            try:
                rows = s.list_cases(name, include_deleted=request.args.get("all") == "1",
                                    limit=int(request.args.get("limit", 200)))
            except (CSWebError, ValueError, KeyError) as e:
                return fail(e)
            return jsonify({
                "counts": s.counts(name),
                "cases": [{"key": r.key, "guid": r.guid, "dirty": r.dirty,
                           "deleted": r.deleted, "on_server": r.on_server,
                           "updated_at": r.updated_at, "data": r.data} for r in rows],
            })

    @app.post("/ui/<name>/cases")
    def add_case(name):
        with session() as s:
            try:
                row = {k: v for k, v in (request.json or {}).items() if v not in ("", None)}
                case = s.add_case(name, row)
            except (CSWebError, ValueError, KeyError) as e:
                return fail(e)
            return jsonify({"key": case.key, "dirty": case.dirty})

    @app.delete("/ui/<name>/cases/<path:key>")
    def remove_case(name, key):
        with session() as s:
            try:
                s.remove_case(name, key)
            except (CSWebError, ValueError, KeyError) as e:
                return fail(e)
            return jsonify({"removed": key.strip()})

    @app.post("/ui/<name>/sync-down")
    def sync_down(name):
        with session() as s:
            try:
                return jsonify(s.sync_down(name, universe=request.args.get("universe")))
            except (CSWebError, ValueError, KeyError) as e:
                return fail(e)

    @app.post("/ui/<name>/sync-up")
    def sync_up(name):
        with session() as s:
            try:
                return jsonify(s.sync_up(name, boost=request.args.get("boost") == "1"))
            except (CSWebError, ValueError, KeyError) as e:
                return fail(e)

    @app.get("/ui/log")
    def log():
        with session() as s:
            return jsonify(s.store.history(30))

    return app


def run(settings: Settings | None = None, host=None, port=None):
    settings = settings or Settings.from_env()
    app = create_app(settings)
    print(f"csync web UI on http://{host or settings.web_host}:{port or settings.web_port}")
    app.run(host=host or settings.web_host, port=port or settings.web_port, debug=False)


if __name__ == "__main__":
    run()
