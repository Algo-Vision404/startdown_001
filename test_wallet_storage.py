import json
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

    def test_corrupt_address_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as data_dir:
            store = WalletStore(data_dir)
            store.create("alice")
            with open(store.filepath, "r") as file:
                wallets = json.load(file)
            wallets["alice"]["address"] = "QR_CORRUPTED"
            with open(store.filepath, "w") as file:
                json.dump(wallets, file)

            restored = WalletStore(data_dir)

        self.assertIsNone(restored.get("alice"))


if __name__ == "__main__":
    unittest.main()
