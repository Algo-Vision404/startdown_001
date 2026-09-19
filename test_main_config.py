import importlib
import os
import unittest


class TestMainConfig(unittest.TestCase):
    def test_ws_port_can_be_overridden_by_environment(self):
        old_ws = os.environ.get("WS_PORT")
        old_api = os.environ.get("API_PORT")
        old_port = os.environ.get("PORT")
        try:
            os.environ["WS_PORT"] = "8123"
            os.environ.pop("API_PORT", None)
            os.environ.pop("PORT", None)
            import main
            importlib.reload(main)
            self.assertEqual(main.WS_PORT, 8123)
            self.assertGreaterEqual(main.API_PORT, 9000)
        finally:
            if old_ws is None:
                os.environ.pop("WS_PORT", None)
            else:
                os.environ["WS_PORT"] = old_ws
            if old_api is None:
                os.environ.pop("API_PORT", None)
            else:
                os.environ["API_PORT"] = old_api
            if old_port is None:
                os.environ.pop("PORT", None)
            else:
                os.environ["PORT"] = old_port
            import main
            importlib.reload(main)

    def test_port_env_only_changes_api_port_not_ws_port(self):
        old_ws = os.environ.get("WS_PORT")
        old_api = os.environ.get("API_PORT")
        old_port = os.environ.get("PORT")
        try:
            os.environ.pop("WS_PORT", None)
            os.environ.pop("API_PORT", None)
            os.environ["PORT"] = "9010"
            import main
            importlib.reload(main)
            self.assertEqual(main.API_PORT, 9010)
            self.assertEqual(main.WS_PORT, 8000)
        finally:
            if old_ws is None:
                os.environ.pop("WS_PORT", None)
            else:
                os.environ["WS_PORT"] = old_ws
            if old_api is None:
                os.environ.pop("API_PORT", None)
            else:
                os.environ["API_PORT"] = old_api
            if old_port is None:
                os.environ.pop("PORT", None)
            else:
                os.environ["PORT"] = old_port
            import main
            importlib.reload(main)

    def test_api_port_falls_back_to_port_env(self):
        old_ws = os.environ.get("WS_PORT")
        old_api = os.environ.get("API_PORT")
        old_port = os.environ.get("PORT")
        try:
            os.environ.pop("WS_PORT", None)
            os.environ.pop("API_PORT", None)
            os.environ["PORT"] = "9015"
            import main
            importlib.reload(main)
            self.assertEqual(main.API_PORT, 9015)
            self.assertEqual(main.WS_PORT, 8000)
        finally:
            if old_ws is None:
                os.environ.pop("WS_PORT", None)
            else:
                os.environ["WS_PORT"] = old_ws
            if old_api is None:
                os.environ.pop("API_PORT", None)
            else:
                os.environ["API_PORT"] = old_api
            if old_port is None:
                os.environ.pop("PORT", None)
            else:
                os.environ["PORT"] = old_port
            import main
            importlib.reload(main)

    def test_find_free_port_skips_occupied_ports(self):
        import main

        real_socket = main.socket.socket

        class FakeSocket:
            def __init__(self, *args, **kwargs):
                self.bind_calls = 0

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def bind(self, address):
                self.bind_calls += 1
                if address[1] == 9000:
                    raise OSError("occupied")
                if address[1] == 9001:
                    return
                raise OSError("unexpected")

        main.socket.socket = FakeSocket
        try:
            picked = main._port_from_env("WS_PORT", 9000)
            self.assertEqual(picked, 9001)
            self.assertGreaterEqual(picked, 9000)
        finally:
            main.socket.socket = real_socket


if __name__ == "__main__":
    unittest.main()
