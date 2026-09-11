"""Isolated loopback TailCam/AI processes for capture integration tests."""

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--peer", default="")
    parser.add_argument("--storage", default="")
    parser.add_argument("--ai", default="")
    parser.add_argument("--roles", default=None, help="Comma-separated roles; empty means hub")
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    if args.name == "mock-ai":
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path != "/api/generate" or not body.get("images"):
                    self.send_error(400)
                    return
                with (args.root / "requests.jsonl").open("a") as file:
                    # Keep test evidence of routing without copying image payloads.
                    file.write(json.dumps({"model": body["model"], "images": len(body["images"])})
                               + "\n")
                result = json.dumps({"response": json.dumps({
                    "state": "healthy", "confidence": 0.9, "description": "Synthetic fixture",
                })}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(result)))
                self.end_headers()
                self.wfile.write(result)

        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
        return

    os.environ.update({
        "TAILCAM_SYNTHETIC": "1", "TAILCAM_LOW_POWER": "0", "TAILCAM_HOST": args.name,
        "TAILCAM_DATA_DIR": str(args.root / "data"),
        "TAILCAM_CONFIG_DIR": str(args.root / "config"), "TAILCAM_PEERS": "",
    })
    os.environ.pop("TAILCAM_CONFIG", None)
    import uvicorn

    from tailcam.config import AppConfig
    from tailcam.web.app import create_app

    config = AppConfig()
    if args.roles is not None:
        config.node.roles = [role.strip() for role in args.roles.split(",") if role.strip()]
    config.server.port = args.port
    config.tailscale.auto_serve = False
    config.peers.auto_discover = False
    config.peers.static = [args.peer] if args.peer else []
    config.storage.node = args.storage
    config.storage.media_dir = str(args.root / "media")
    config.detection.enabled = False
    config.ai.enabled = bool(args.ai)
    config.ai.base_url = args.ai or "http://127.0.0.1:1"
    config.ai.model = "test-printer"
    config.ai.timeout = 2
    config.plugins.load_dropins = False
    app = create_app(config)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
