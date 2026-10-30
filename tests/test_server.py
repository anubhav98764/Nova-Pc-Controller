import unittest

from windows_companion.server import (
    ClientSession,
    ServerState,
    handle_command,
    validate_discovery_payload,
)


class ServerProtocolTests(unittest.TestCase):
    def setUp(self):
        self.state = ServerState(
            server_name="test-host",
            host="127.0.0.1",
            tcp_port=8988,
            udp_port=8989,
            pairing_token="token123",
        )
        self.session = ClientSession()

    def test_requires_authentication_before_commands(self):
        response = handle_command({"command": "ping"}, self.session, self.state)
        self.assertFalse(response["ok"])
        self.assertEqual("UNAUTHORIZED", response["error"]["code"])

    def test_authenticate_and_ping(self):
        bad_auth = handle_command({"command": "authenticate", "token": "bad"}, self.session, self.state)
        self.assertFalse(bad_auth["ok"])

        good_auth = handle_command({"command": "authenticate", "token": "token123"}, self.session, self.state)
        self.assertTrue(good_auth["ok"])
        self.assertTrue(self.session.authenticated)

        ping = handle_command({"command": "ping"}, self.session, self.state)
        self.assertTrue(ping["ok"])
        self.assertEqual("pong", ping["data"]["message"])

    def test_clear_activity(self):
        handle_command({"command": "authenticate", "token": "token123"}, self.session, self.state)
        handle_command({"command": "ping"}, self.session, self.state)
        history = handle_command({"command": "recent_activity"}, self.session, self.state)
        self.assertTrue(history["ok"])
        self.assertGreaterEqual(len(history["data"]["items"]), 1)

        cleared = handle_command({"command": "clear_activity"}, self.session, self.state)
        self.assertTrue(cleared["ok"])

    def test_discovery_payload_validation(self):
        self.assertTrue(validate_discovery_payload(b"NOVA_DISCOVER"))
        self.assertTrue(
            validate_discovery_payload(
                b'{"type":"discover","protocol":"nova-pc-controller"}'
            )
        )
        self.assertFalse(validate_discovery_payload(b"hello"))


if __name__ == "__main__":
    unittest.main()
