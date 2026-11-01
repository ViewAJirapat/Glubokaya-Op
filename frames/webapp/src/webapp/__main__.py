"""Serve the app: `python -m webapp` (HOST and PORT env vars; default 127.0.0.1:8000)."""

import os
import socket
import socketserver
import sys
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from webapp.app import create_app

WINDOWS = sys.platform == "win32"


class ThreadingWSGIServer(socketserver.ThreadingMixIn, WSGIServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets two servers share a port; fail loudly instead.
    allow_reuse_address = not WINDOWS

    def server_bind(self) -> None:
        if WINDOWS:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class QuietHandler(WSGIRequestHandler):
    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        if not str(code).startswith(("2", "3")):
            super().log_request(code, size)


def main() -> None:
    app = create_app()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    with make_server(host, port, app.wsgi, ThreadingWSGIServer, QuietHandler) as server:
        for r in app.routes:
            print(f"{r.method:<6} {r.pattern:<32} feature {r.feature}")
        print(f"webapp on http://{host}:{port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
