import json
import tempfile
import unittest

from wallet import QuantumWallet, WalletError


class TestKeyGeneration(unittest.TestCase):
    def test_new_wallet_has_keys_and_a_derived_address(self):
        wallet = QuantumWallet()

        self.assertTrue(len(wallet.public_key) > 0)
        self.assertTrue(len(wallet.private_key) > 0)
        self.assertTrue(wallet.address.startswith("QR_"))

    def test_two_wallets_have_different_keys_and_addresses(self):
        a = QuantumWallet()
        b = QuantumWallet()

        self.assertNotEqual(a.public_key, b.public_key)
        self.assertNotEqual(a.address, b.address)


class TestAddressDerivation(unittest.TestCase):
    def test_address_derivation_is_deterministic(self):
        wallet = QuantumWallet()

        self.assertEqual(
            QuantumWallet._derive_address(wallet.public_key),
            wallet.address,
        )

    def test_different_public_keys_derive_different_addresses(self):
        a = QuantumWallet()
        b = QuantumWallet()

        self.assertNotEqual(
            QuantumWallet._derive_address(a.public_key),
            QuantumWallet._derive_address(b.public_key),
        )


class TestSignAndVerify(unittest.TestCase):
    def test_a_valid_signature_verifies(self):
        wallet = QuantumWallet()
        message = b"a message to sign"

        signature = wallet.sign(message)

        self.assertTrue(
            QuantumWallet.verify(message, signature, wallet.public_key)
        )

    def test_sign_rejects_non_bytes_input(self):
        wallet = QuantumWallet()

        with self.assertRaises(WalletError):
            wallet.sign("not bytes")

    def test_verify_rejects_tampered_data(self):
        wallet = QuantumWallet()
        signature = wallet.sign(b"original message")

        self.assertFalse(
            QuantumWallet.verify(b"different message", signature, wallet.public_key)
        )

    def test_verify_rejects_a_signature_from_a_different_wallet(self):
        signer = QuantumWallet()
        other = QuantumWallet()
        message = b"a message"
        signature = signer.sign(message)

        self.assertFalse(
            QuantumWallet.verify(message, signature, other.public_key)
        )

    def test_verify_rejects_corrupted_signature_bytes(self):
        wallet = QuantumWallet()
        message = b"a message"
        signature = bytearray(wallet.sign(message))
        signature[0] ^= 0xFF  # flip a bit

        self.assertFalse(
            QuantumWallet.verify(message, bytes(signature), wallet.public_key)
        )

    def test_verify_never_raises_on_garbage_input(self):
        # verify() is documented to never raise -- any node on the
        # network can call it with attacker-supplied bytes.
        self.assertFalse(QuantumWallet.verify(b"data", b"", b""))
        self.assertFalse(QuantumWallet.verify(b"data", b"\x00" * 10, b"\x00" * 10))


class TestFilePersistence(unittest.TestCase):
    def test_saved_wallet_round_trips_through_load(self):
        wallet = QuantumWallet()
        with tempfile.TemporaryDirectory() as data_dir:
            path = f"{data_dir}/wallet.json"
            wallet.save(path)

            restored = QuantumWallet.load(path)

        self.assertEqual(restored.address, wallet.address)
        self.assertEqual(restored.public_key, wallet.public_key)
        self.assertEqual(restored.private_key, wallet.private_key)

        message = b"signed after reload"
        signature = restored.sign(message)
        self.assertTrue(
            QuantumWallet.verify(message, signature, restored.public_key)
        )

    def test_load_raises_for_a_missing_file(self):
        with self.assertRaises(WalletError):
            QuantumWallet.load("/nonexistent/path/wallet.json")

    def test_load_rejects_a_different_algorithm(self):
        wallet = QuantumWallet()
        with tempfile.TemporaryDirectory() as data_dir:
            path = f"{data_dir}/wallet.json"
            wallet.save(path)

            with open(path) as f:
                data = json.load(f)
            data["algorithm"] = "Ed25519"
            with open(path, "w") as f:
                json.dump(data, f)

            with self.assertRaises(WalletError):
                QuantumWallet.load(path)

    def test_load_rejects_an_address_that_does_not_match_the_public_key(self):
        # This is the exact gap: load() used to trust the stored
        # address outright instead of re-deriving and checking it
        # against the public key, unlike WalletStore._load() (which
        # already performs this same check for its own load path).
        wallet = QuantumWallet()
        other = QuantumWallet()
        with tempfile.TemporaryDirectory() as data_dir:
            path = f"{data_dir}/wallet.json"
            wallet.save(path)

            with open(path) as f:
                data = json.load(f)
            data["address"] = other.address  # mismatched on purpose
            with open(path, "w") as f:
                json.dump(data, f)

            with self.assertRaises(WalletError):
                QuantumWallet.load(path)


if __name__ == "__main__":
    unittest.main()
