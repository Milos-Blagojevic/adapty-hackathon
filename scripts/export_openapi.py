"""Write docs/openapi.json: the JSON API only (no HTML pages), from the running FastAPI app's schema."""

import json
import tempfile
from pathlib import Path

from resolve import config, web

app = web.create_app(Path(tempfile.mkdtemp()) / "openapi.sqlite", demo=True, seed_file=config.ROOT / "none.json")
spec = app.openapi()
spec["paths"] = {p: ops for p, ops in spec["paths"].items() if p.startswith("/api/") or p in ("/chat", "/feedback", "/healthz")}
spec["info"] = {
    "title": "Resolve JSON API",
    "version": "2",
    "description": "Contract for the mobile app and any UI. Mobile needs the 'mobile' tag: POST /chat, GET /api/chat/{session_id} "
    "(poll for support replies after a handoff), GET /api/users, POST /feedback. Default dev server: http://<laptop-LAN-IP>:8010.",
}
out = config.ROOT / "docs" / "openapi.json"
out.write_text(json.dumps(spec, indent=1) + "\n")
print(f"wrote {out} ({len(spec['paths'])} paths)")
