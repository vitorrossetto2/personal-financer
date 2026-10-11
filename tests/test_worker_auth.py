import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from flask import Flask, session
from worker_auth import WorkerGoogleOAuth


class WorkerOAuthTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SECRET_KEY="test", GOOGLE_CLIENT_ID="client", GOOGLE_CLIENT_SECRET="secret")
        self.oauth = WorkerGoogleOAuth()

    def test_redirect_uses_state_and_pkce(self):
        with self.app.test_request_context():
            response = self.oauth.authorize_redirect("https://example.com/auth/google/callback")
            query = parse_qs(urlparse(response.location).query)
            self.assertEqual(query["state"], [session["google_oauth"]["state"]])
            self.assertEqual(query["code_challenge_method"], ["S256"])
            self.assertNotEqual(query["code_challenge"][0], session["google_oauth"]["verifier"])

    def test_callback_rejects_invalid_or_expired_state_without_http(self):
        for state, created in (("wrong", time.time()), ("correct", time.time() - 601)):
            with self.subTest(state=state), self.app.test_request_context("/?state=" + state + "&code=code"):
                session["google_oauth"] = {"state": "correct", "created": created}
                with patch.object(self.oauth, "_json") as http:
                    with self.assertRaises(ValueError):
                        self.oauth.authorize_access_token()
                    http.assert_not_called()

    def test_callback_uses_google_userinfo_and_consumes_state(self):
        with self.app.test_request_context("/?state=correct&code=code"):
            session["google_oauth"] = {"state": "correct", "created": time.time(),
                                       "verifier": "verifier", "callback": "https://example.com/callback"}
            user = {"sub": "123", "email": "owner@example.com", "email_verified": True}
            with patch.object(self.oauth, "_json", side_effect=[{"access_token": "token"}, user]) as http:
                self.assertEqual(self.oauth.authorize_access_token(), {"userinfo": user})
                self.assertEqual(http.call_count, 2)
                self.assertIn("code_verifier=verifier", http.call_args_list[0].kwargs["body"])
                self.assertEqual(http.call_args_list[1].kwargs["headers"]["Authorization"], "Bearer token")
                with self.assertRaises(ValueError):
                    self.oauth.authorize_access_token()
