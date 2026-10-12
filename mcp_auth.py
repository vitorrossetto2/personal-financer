"""Require a private bearer token before any MCP HTTP request is handled."""
import secrets

from starlette.responses import JSONResponse


class MCPTokenAuth:
    def __init__(self, app, token: str):
        self.app = app
        self.token = token.encode("utf-8")

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == "/mcp" or path.startswith("/mcp/")):
            return await self.app(scope, receive, send)
        headers = {"Cache-Control": "no-store"}
        if not self.token:
            response = JSONResponse({"error": "MCP authentication is not configured"}, status_code=503, headers=headers)
        else:
            authorization = [value for name, value in scope.get("headers", []) if name.lower() == b"authorization"]
            scheme, _, credential = authorization[0].partition(b" ") if len(authorization) == 1 else (b"", b"", b"")
            if scheme.lower() == b"bearer" and secrets.compare_digest(credential, self.token):
                return await self.app(scope, receive, send)
            headers["WWW-Authenticate"] = "Bearer"
            response = JSONResponse({"error": "Unauthorized"}, status_code=401, headers=headers)
        return await response(scope, receive, send)
