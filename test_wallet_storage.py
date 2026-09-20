import tempfile
import unittest

from storage import WalletStore
from wallet import QuantumWallet


class TestWalletPersistence(unittest.TestCase):
    def test_reloaded_wallet_can_sign_and_verify(self):
        with tempfile.TemporaryDirectory() as data_dir:
            first_store = WalletStore(data_dir)
            original = first_store.create("alice")
            second_store = WalletStore(data_dir)
            restored = second_store.get("alice")

        self.assertIsNotNone(restored)
        self.assertEqual(restored.address, original.address)
        signature = restored.sign(b"wallet persistence")
        self.assertTrue(
            QuantumWallet.verify(
                b"wallet persistence",
                signature,
                restored.public_key,
            )
        )


if __name__ == "__main__":
    unittest.main()
