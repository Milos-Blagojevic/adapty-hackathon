"""`python -m resolve [--host 127.0.0.1] [--port 8000]`: one uvicorn process."""

from __future__ import annotations

import argparse

import uvicorn

from resolve import config
from resolve.web import build_services_from_env, create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m resolve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    db_path, demo, adapty_client, anthropic_client, model, runner = build_services_from_env()
    app = create_app(db_path, demo, adapty_client, anthropic_client, model, ab_runner=runner)
    mode = "DEMO MODE (snapshot, no keys)" if demo else f"live · Adapty app {config.APP_ID} · model {model or 'none'}"
    print(f"Resolve on http://{args.host}:{args.port} · {mode}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
