import unittest

from mcp_auth import MCPTokenAuth


class MCPTokenAuthTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, *, token="private-token", path="/mcp", headers=(), method="POST"):
        called = []
        messages = []

        async def downstream(scope, receive, send):
            called.append(scope)

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            messages.append(message)

        await MCPTokenAuth(downstream, token)(
            {"type": "http", "path": path, "method": method, "headers": headers}, receive, send,
        )
        return called, messages

    async def test_all_methods_and_subpaths_require_token(self):
        for method in ("GET", "POST", "DELETE", "OPTIONS"):
            for path in ("/mcp", "/mcp/", "/mcp/messages"):
                for headers in ((), [(b"authorization", b"Bearer wrong")],
                                [(b"authorization", b"Basic private-token")],
                                [(b"authorization", b"Bearer private-token"), (b"authorization", b"Bearer wrong")]):
                    with self.subTest(method=method, path=path, headers=headers):
                        called, messages = await self.request(method=method, path=path, headers=headers)
                        self.assertFalse(called)
                        self.assertEqual(messages[0]["status"], 401)
                        self.assertIn((b"www-authenticate", b"Bearer"), messages[0]["headers"])

    async def test_missing_configuration_denies_even_with_header(self):
        called, messages = await self.request(token="", headers=[(b"authorization", b"Bearer private-token")])
        self.assertFalse(called)
        self.assertEqual(messages[0]["status"], 503)

    async def test_correct_token_allows_request(self):
        called, messages = await self.request(headers=[(b"authorization", b"bearer private-token")])
        self.assertEqual(len(called), 1)
        self.assertFalse(messages)

    async def test_web_routes_are_unaffected(self):
        for path in ("/", "/login", "/cartoes", "/mcp-other"):
            called, messages = await self.request(token="", path=path)
            self.assertEqual(len(called), 1)
            self.assertFalse(messages)
