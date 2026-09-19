import importlib
import os
import unittest


class TestMainConfig(unittest.TestCase):
    def test_ws_port_can_be_overridden_by_environment(self):
        old_ws = os.environ.get("WS_PORT")
        old_api = os.environ.get("API_PORT")
        old_port = os.environ.get("PORT")
        ws_port = 19123
        try:
            os.environ["WS_PORT"] = str(ws_port)
            os.environ.pop("API_PORT", None)
            os.environ.pop("PORT", None)
            import main
            importlib.reload(main)
            self.assertEqual(main.WS_PORT, ws_port)
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
        api_port = 19110
        ws_port = 19120
        try:
            os.environ["WS_PORT"] = str(ws_port)
            os.environ.pop("API_PORT", None)
            os.environ["PORT"] = str(api_port)
            import main
            importlib.reload(main)
            self.assertEqual(main.API_PORT, api_port)
            self.assertEqual(main.WS_PORT, ws_port)
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
        api_port = 19115
        try:
            os.environ.pop("WS_PORT", None)
            os.environ.pop("API_PORT", None)
            os.environ["PORT"] = str(api_port)
            import main
            importlib.reload(main)
            self.assertEqual(main.API_PORT, api_port)
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
        old_ws = os.environ.pop("WS_PORT", None)
        old_api = os.environ.pop("API_PORT", None)
        old_port = os.environ.pop("PORT", None)

        class FakeSocket:
            def __init__(self, *args, **kwargs):
                self.bind_calls = 0

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def bind(self, address):
                self.bind_calls += 1
                if address == ("0.0.0.0", 19200):
                    raise OSError("occupied")
                if address == ("0.0.0.0", 19201):
                    return
                raise OSError(f"unexpected {address}")

        main.socket.socket = FakeSocket
        try:
            picked = main._port_from_env("WS_PORT", 19200)
            self.assertEqual(picked, 19201)
            self.assertGreaterEqual(picked, 19200)
        finally:
            main.socket.socket = real_socket
            if old_ws is not None:
                os.environ["WS_PORT"] = old_ws
            if old_api is not None:
                os.environ["API_PORT"] = old_api
            if old_port is not None:
                os.environ["PORT"] = old_port

    def test_port_check_uses_wildcard_address_for_windows_conflicts(self):
        import main

        real_socket = main.socket.socket
        old_ws = os.environ.pop("WS_PORT", None)
        old_api = os.environ.pop("API_PORT", None)
        old_port = os.environ.pop("PORT", None)

        class FakeSocket:
            def __init__(self, *args, **kwargs):
                self.bind_calls = 0

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def bind(self, address):
                if address == ("0.0.0.0", 19210):
                    raise OSError("wildcard port already occupied")
                if address == ("0.0.0.0", 19211):
                    return
                raise OSError(f"unexpected {address}")

        main.socket.socket = FakeSocket
        try:
            os.environ["WS_PORT"] = "19210"
            self.assertEqual(main._port_from_env("WS_PORT", 19210), 19211)
        finally:
            main.socket.socket = real_socket
            if old_ws is not None:
                os.environ["WS_PORT"] = old_ws
            else:
                os.environ.pop("WS_PORT", None)
            if old_api is not None:
                os.environ["API_PORT"] = old_api
            else:
                os.environ.pop("API_PORT", None)
            if old_port is not None:
                os.environ["PORT"] = old_port
            else:
                os.environ.pop("PORT", None)

    def test_wallet_signing_and_verification_roundtrip(self):
        from wallet import QuantumWallet

        wallet = QuantumWallet()
        payload = b"hello blockchain"
        signature = wallet.sign(payload)

        self.assertTrue(QuantumWallet.verify(payload, signature, wallet.public_key))
        self.assertNotEqual(signature, b"")


if __name__ == "__main__":
    unittest.main()
