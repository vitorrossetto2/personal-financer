from __future__ import annotations

import os

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.routing import Mount

from app import app as flask_app
from mcp_server import server as mcp_server


host = os.environ.get("NOVOSALDO_HOST", "127.0.0.1")
mcp_setting = os.environ.get("NOVOSALDO_MCP_ENABLED")
mcp_enabled = (
    mcp_setting.lower() in {"1", "true", "yes"}
    if mcp_setting is not None
    else host in {"127.0.0.1", "localhost", "::1"}
)

if mcp_enabled:
    mcp_http_app = mcp_server.streamable_http_app(streamable_http_path="/mcp", host=host)
    app = Starlette(
        routes=[*mcp_http_app.routes, Mount("/", app=WSGIMiddleware(flask_app))],
        lifespan=mcp_http_app.router.lifespan_context,
    )
else:
    app = Starlette(routes=[Mount("/", app=WSGIMiddleware(flask_app))])


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=host,
        port=int(os.environ.get("NOVOSALDO_PORT", "5000")),
        log_level="info",
    )
