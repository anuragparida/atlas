"""Tiny mock openwebui + ntfy for the notifier smoke test.

Serves:
- GET /api/v1/auths/me/ → 200 {id: "u-anurag", ...}
- GET /api/v1/chats/?page=1 → 200 {chats: [{id, title, chat: {chat_history: [...]}}]}
- GET /v1/health → 200
- POST /<topic> → 200 + records the publish

Run: python3 tests/smoke_mock.py [port]
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PUBLISHES: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A002
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/v1/auths/me/":
            self._send(200, {"id": "u-anurag", "email": "a@x", "name": "A"})
            return
        if self.path == "/api/v1/chats/?page=1":
            # Return one idle chat (done=True).
            self._send(
                200,
                {
                    "chats": [
                        {
                            "id": "chat-1",
                            "title": "hello",
                            "updated_at": 1_700_000_000,
                            "chat": {
                                "content": "hi",
                                "models": ["llama3"],
                                "chat_history": [
                                    {
                                        "role": "assistant",
                                        "content": "world",
                                        "done": True,
                                        "updated_at": 1_700_000_000,
                                    }
                                ],
                            },
                        }
                    ],
                    "total": 1,
                },
            )
            return
        if self.path == "/v1/health":
            self._send(200, {"healthy": True})
            return
        self._send(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/atlas-u-anurag":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            PUBLISHES.append(
                {
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": body,
                }
            )
            self._send(200, {"event": "message", "id": "m1"})
            return
        self._send(404, {"detail": "not found"})

    def _send(self, code: int, body) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock listening on 127.0.0.1:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
