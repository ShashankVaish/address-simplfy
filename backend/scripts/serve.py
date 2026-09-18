"""Run the API on your laptop, no AWS and no SAM.

Wraps the two Lambda handlers in a plain HTTP server so the console (or curl)
can call `POST /v1/resolve`, `GET /health`, `GET /v1/queue` and
`POST /v1/feedback` at http://localhost:8000. Same code path as the deployed
Lambda -- only the transport differs.

    set PROVIDER=local            (PowerShell: $env:PROVIDER="local")
    python -m scripts.serve
    python -m scripts.serve --stack R2 --port 8000

Loads the landmark index from eval/data/landmarks.jsonl if it exists, so
retrieval stacks (R1, R2, C, D, E) work out of the box.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "layers/common/python"))
sys.path.insert(0, str(ROOT))


def load_handler(relative: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--stack", default=os.environ.get("SERVING_STACK", "R2"))
    args = ap.parse_args()

    os.environ.setdefault("PROVIDER", "local")
    os.environ["SERVING_STACK"] = args.stack

    resolver = load_handler("functions/resolver/app.py", "resolver_app")
    queue = load_handler("functions/queue_api/app.py", "queue_app")
    # One in-memory store shared by both handlers, so the queue sees what the
    # resolver wrote -- exactly what DynamoDB provides in the cloud.
    queue.PROVIDERS.__dict__["store"] = resolver.PROVIDERS.store

    index_path = ROOT / "eval/data/landmarks.jsonl"
    if index_path.exists() and resolver.SERVING_STACK.uses_retrieval:
        from scripts.warm_landmarks import load_landmarks

        n = resolver.PROVIDERS.search.upsert(load_landmarks(index_path))
        print(f"landmark index: {n} records loaded")
    elif resolver.SERVING_STACK.uses_retrieval:
        print("warning: eval/data/landmarks.jsonl missing; run scripts.warm_landmarks")

    class Handler(BaseHTTPRequestHandler):
        def _dispatch(self, method: str) -> None:
            url = urlparse(self.path)
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length).decode("utf-8") if length else None
            event = {
                "rawPath": url.path,
                "requestContext": {"http": {"path": url.path, "method": method}},
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "queryStringParameters": {
                    k: v[0] for k, v in parse_qs(url.query).items()
                },
                "pathParameters": (
                    {"order_id": url.path.rsplit("/", 1)[-1]}
                    if "/orders/" in url.path
                    else {}
                ),
                "body": body,
            }
            target = (
                queue
                if url.path.startswith(("/v1/queue", "/v1/feedback", "/v1/orders"))
                else resolver
            )
            response = target.handler(event, None)
            payload = response.get("body", "").encode("utf-8")
            self.send_response(response.get("statusCode", 200))
            for key, value in (response.get("headers") or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_OPTIONS(self) -> None:
            self._dispatch("OPTIONS")

        def log_message(self, fmt: str, *a) -> None:
            print(f"  {self.command} {self.path} -> {a[1] if len(a) > 1 else ''}")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(
        f"PataSetu API on http://localhost:{args.port}  (stack {args.stack}, PROVIDER={os.environ['PROVIDER']})"
    )
    example = '{"raw":"h no 14 behind shiv mandir ramesh nagar delhi 110015"}'
    print(
        f"try:  curl -X POST http://localhost:{args.port}/v1/resolve "
        f"-H 'content-type: application/json' -d '{example}'"
    )
    print("Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
