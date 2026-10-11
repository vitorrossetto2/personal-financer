"""Google OAuth for Workers, using Fetch instead of socket-based requests."""
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

from flask import current_app, redirect, request, session


class WorkerGoogleOAuth:
    def authorize_redirect(self, callback):
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        session["google_oauth"] = {
            "state": state, "verifier": verifier, "callback": callback,
            "created": time.time(),
        }
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": current_app.config["GOOGLE_CLIENT_ID"],
            "redirect_uri": callback, "response_type": "code",
            "scope": "openid email profile", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
        }))

    def authorize_access_token(self):
        pending = session.pop("google_oauth", None)
        state = request.args.get("state", "")
        if (not pending or not state
                or not secrets.compare_digest(state, pending["state"])
                or not 0 <= time.time() - pending["created"] <= 600
                or not request.args.get("code") or request.args.get("error")):
            raise ValueError("Invalid or expired Google authorization")
        token = self._json("https://oauth2.googleapis.com/token", method="POST", headers={
            "Content-Type": "application/x-www-form-urlencoded",
        }, body=urlencode({
            "client_id": current_app.config["GOOGLE_CLIENT_ID"],
            "client_secret": current_app.config["GOOGLE_CLIENT_SECRET"],
            "code": request.args["code"], "code_verifier": pending["verifier"],
            "redirect_uri": pending["callback"], "grant_type": "authorization_code",
        }))
        # Obtain verified identity directly from Google's authenticated UserInfo
        # endpoint. Never trust an unverified, locally decoded ID token.
        userinfo = self._json("https://openidconnect.googleapis.com/v1/userinfo", headers={
            "Authorization": "Bearer " + token["access_token"],
        })
        if not userinfo.get("sub"):
            raise ValueError("Google did not return a subject")
        return {"userinfo": userinfo}

    @staticmethod
    def _json(url, **options):
        from pyodide.ffi import run_sync
        from workers import fetch

        async def fetch_json():
            response = await fetch(url, **options)
            if not response.ok:
                raise ValueError("Google authorization request failed")
            return await response.json()

        return run_sync(fetch_json())
