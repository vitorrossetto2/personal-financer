"""Cloudflare-only entrypoint; local development still uses run.py."""
from flask import Response, request
from jinja2 import DictLoader
from pyodide.ffi import run_sync
from workers import wsgi

from app import app
from worker_templates import TEMPLATES

app.jinja_loader = DictLoader(TEMPLATES)


def static_asset(filename):
    env = request.environ["workers.env"]
    response = run_sync(env.ASSETS.fetch(request.url))
    body = run_sync(response.bytes())
    headers = [(str(key), str(value)) for key, value in response.headers.items()]
    return Response(body, status=response.status, headers=headers)


app.view_functions["static"] = static_asset


class WorkerConfiguration:
    def __init__(self, application):
        self.application = application

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path == "/mcp" or path.startswith("/mcp/"):
            return Response("Not found", status=404)(environ, start_response)
        env = environ["workers.env"]
        secret = str(getattr(env, "NOVOSALDO_SECRET_KEY", "") or "")
        if not secret:
            return Response("Configure NOVOSALDO_SECRET_KEY before using this Worker.", status=503)(environ, start_response)
        # Bindings are constant for a deployed Worker version. Configure before
        # Flask opens its signed session, including on the OAuth callback.
        app.config.update(
            SECRET_KEY=secret, WORKER_SECRET_CONFIGURED=True,
            GOOGLE_CLIENT_ID=str(getattr(env, "GOOGLE_CLIENT_ID", "") or "").strip(),
            GOOGLE_CLIENT_SECRET=str(getattr(env, "GOOGLE_CLIENT_SECRET", "") or "").strip(),
            ALLOWED_GOOGLE_EMAIL=str(getattr(env, "NOVOSALDO_ALLOWED_EMAIL", "") or "").strip().casefold(),
            SESSION_COOKIE_SECURE=environ.get("wsgi.url_scheme") == "https",
            LOCAL_AUTH_DISABLED=False, AUTH_DISABLED_FOR_TESTS=False,
        )
        return self.application(environ, start_response)


Default = wsgi.entrypoint(WorkerConfiguration(app))
