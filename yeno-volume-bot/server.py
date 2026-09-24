#!/usr/bin/env python3
"""HTTP /decide server for the YeNo x Builderr Volume Bot Challenge.

Exposes ``POST http://127.0.0.1:8080/decide`` and delegates every decision to
``bot.decide``. Only the strategy in ``bot.py`` is ours; cash, fills, fees,
volume and position accounting remain evaluator-owned.

Usage:
    python server.py [--host 127.0.0.1] [--port 8080]
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from bot import decide


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/decide":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            observation = json.loads(self.rfile.read(length))
            response = decide(observation)
            body = json.dumps(response, separators=(",", ":")).encode()
        except Exception:
            self.send_error(400)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
