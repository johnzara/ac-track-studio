#!/usr/bin/env python3
"""Serve the AC Track Studio planner on http://127.0.0.1:8770 and save briefs into tracks/.

    python3 serve.py [--port 8770] [--open]

Standard library only. Binds to 127.0.0.1 (this computer only). API:
  GET  /api/info              studio paths
  GET  /api/tracks            saved tracks (tracks/<slug>/track.json)
  GET  /api/tracks/<slug>     one track.json
  POST /api/tracks/<slug>     {"files": {"track.json": "...", "BRIEF.md": "...", "route.geojson": "..."}}
Serving over http (not file://) also lets the OSM tile servers and a referrer-restricted Google
key work.
"""

import argparse
import json
import re
import sys
import webbrowser
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLANNER = ROOT / "planner"
TRACKS = ROOT / "tracks"
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ALLOWED = {"track.json", "BRIEF.md", "route.geojson"}
MAX_BODY = 64 * 1024 * 1024


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(PLANNER), **kw)

    def log_message(self, fmt, *args):
        if "/api/" in (str(args[0]) if args else ""):
            sys.stderr.write("%s\n" % (fmt % args))

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/info":
            return self._json(200, {"studio_root": str(ROOT), "tracks_dir": str(TRACKS),
                                    "reference_pipeline": str(ROOT / "pipeline"), "version": 1})
        if path == "/api/tracks":
            out = []
            for f in sorted(TRACKS.glob("*/track.json")):
                try:
                    spec = json.loads(f.read_text())
                except (OSError, ValueError):
                    continue
                t = spec.get("track", {})
                out.append({"slug": f.parent.name, "name": t.get("name"), "type": t.get("type"),
                            "length_m": (spec.get("route") or {}).get("length_m"),
                            "updated": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()})
            out.sort(key=lambda x: x["updated"], reverse=True)
            return self._json(200, out)
        m = re.fullmatch(r"/api/tracks/([^/]+)", path)
        if m:
            slug = m.group(1)
            f = TRACKS / slug / "track.json"
            if not SLUG.match(slug) or not f.exists():
                return self._json(404, {"error": "no such track"})
            body = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def do_POST(self):
        m = re.fullmatch(r"/api/tracks/([^/]+)", self.path.split("?")[0])
        if not m:
            return self._json(404, {"error": "not found"})
        slug = m.group(1)
        if not SLUG.match(slug):
            return self._json(400, {"error": "bad slug: use a-z, 0-9, _ and -"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self._json(413, {"error": "empty or too large"})
        try:
            files = json.loads(self.rfile.read(n))["files"]
        except (ValueError, KeyError, TypeError):
            return self._json(400, {"error": "expected {files: {...}}"})
        if not isinstance(files, dict) or not files or not set(files) <= ALLOWED:
            return self._json(400, {"error": f"files must be among {sorted(ALLOWED)}"})
        if "track.json" in files:
            try:
                json.loads(files["track.json"])
            except ValueError:
                return self._json(400, {"error": "track.json is not valid JSON"})
        d = TRACKS / slug
        d.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            if not isinstance(text, str):
                return self._json(400, {"error": f"{name} must be text"})
            tmp = d / (name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(d / name)
        return self._json(200, {"dir": str(d), "files": sorted(files)})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--open", action="store_true", help="open the planner in the default browser")
    a = ap.parse_args()
    TRACKS.mkdir(exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"AC Track Studio planner: {url}  (Ctrl+C to stop)")
    if a.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
