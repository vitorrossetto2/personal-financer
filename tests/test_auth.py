import unittest
from unittest.mock import patch

import app as novosaldo


class GoogleAuthenticationTests(unittest.TestCase):
    def setUp(self):
        novosaldo.app.config.update(
            TESTING=False,
            SECRET_KEY="test-secret",
            GOOGLE_CLIENT_ID="client-id",
            GOOGLE_CLIENT_SECRET="client-secret",
            ALLOWED_GOOGLE_EMAIL="dono@gmail.com",
            LOCAL_AUTH_DISABLED=False,
            AUTH_DISABLED_FOR_TESTS=False,
        )
        self.client = novosaldo.app.test_client()

    def tearDown(self):
        novosaldo.app.config["TESTING"] = False
        novosaldo.app.config["LOCAL_AUTH_DISABLED"] = False
        novosaldo.app.config["AUTH_DISABLED_FOR_TESTS"] = False

    def test_protected_page_redirects_to_login(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")

    def test_local_access_can_skip_login_when_enabled(self):
        novosaldo.app.config["LOCAL_AUTH_DISABLED"] = True

        response = self.client.get("/", environ_base={"REMOTE_ADDR": "127.0.0.1"})

        self.assertEqual(response.status_code, 200)

    def test_remote_access_still_requires_login_when_local_bypass_is_enabled(self):
        novosaldo.app.config["LOCAL_AUTH_DISABLED"] = True

        response = self.client.get("/", environ_base={"REMOTE_ADDR": "192.0.2.10"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")

    def test_allowed_verified_email_creates_session(self):
        userinfo = {
            "sub": "google-user-id",
            "email": "Dono@gmail.com",
            "email_verified": True,
            "name": "Dono",
        }
        with patch.object(novosaldo.google, "authorize_access_token", return_value={"userinfo": userinfo}):
            response = self.client.get("/auth/google/callback")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        with self.client.session_transaction() as session:
            self.assertEqual(session["user"]["email"], "dono@gmail.com")

    def test_other_email_is_denied(self):
        userinfo = {
            "sub": "other-google-user-id",
            "email": "outra@gmail.com",
            "email_verified": True,
        }
        with patch.object(novosaldo.google, "authorize_access_token", return_value={"userinfo": userinfo}):
            response = self.client.get("/auth/google/callback", follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("não tem permissão", response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            self.assertNotIn("user", session)

    def test_unverified_allowed_email_is_denied(self):
        userinfo = {
            "sub": "google-user-id",
            "email": "dono@gmail.com",
            "email_verified": False,
        }
        with patch.object(novosaldo.google, "authorize_access_token", return_value={"userinfo": userinfo}):
            self.client.get("/auth/google/callback")

        with self.client.session_transaction() as session:
            self.assertNotIn("user", session)


if __name__ == "__main__":
    unittest.main()
