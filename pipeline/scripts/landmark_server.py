#!/usr/bin/env python3
"""Local helper for exporting landmark models from their three.js pages.

Serves landmarks/src/ on http://127.0.0.1:8765/ and accepts
  POST /save/<name>.glb   (binary body)  -> landmarks/glb/<name>.glb
Only listens on localhost.
"""

import http.server
import re
from functools import partial

from common import ROOT

SRC, OUT = ROOT / "landmarks" / "src", ROOT / "landmarks" / "glb"


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        m = re.fullmatch(r"/save/([A-Za-z0-9_\-]+\.glb)", self.path)
        if not m:
            self.send_error(404)
            return
        body = self.rfile.read(int(self.headers["Content-Length"]))
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / m.group(1)).write_bytes(body)
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(f"saved {len(body)} bytes".encode())

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", 8765), partial(Handler, directory=str(SRC))).serve_forever()
