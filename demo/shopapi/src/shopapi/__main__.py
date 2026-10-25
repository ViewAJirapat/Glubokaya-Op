"""Serve the app: `python -m shopapi` (PORT env var, default 8000)."""

import os
from wsgiref.simple_server import make_server

from shopapi.app import create_app


def main() -> None:
    app = create_app()
    port = int(os.environ.get("PORT", "8000"))
    with make_server("127.0.0.1", port, app.wsgi) as server:
        for r in app.routes:
            print(f"{r.method:<6} {r.pattern:<32} feature {r.feature}")
        print(f"shopapi on http://127.0.0.1:{port}")
        server.serve_forever()


if __name__ == "__main__":
    main()
