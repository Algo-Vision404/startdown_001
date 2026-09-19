import oqs
import hashlib
import json
import base64
import os


ALGORITHM   = "ML-DSA-65"
ADDR_PREFIX = "QR"
ADDR_LENGTH = 40


class WalletError(Exception):
    pass


class QuantumWallet:
    """
    Post-quantum asymmetric wallet using ML-DSA-65 (Dilithium3).

    Classical wallets rely on ECDSA, which is vulnerable to
    Shor's algorithm on a sufficiently large quantum computer.
    Dilithium3 is a lattice-based signature scheme standardized
    by NIST in 2024 as ML-DSA-65. It provides security against
    both classical and quantum adversaries.

    Key sizes are larger than ECDSA by design:
        Public key  : 1952 bytes
        Private key : 4000 bytes
        Signature   : 3293 bytes

    This is the expected trade-off for quantum resistance.
    """

    def __init__(self):
        self._signer     = oqs.Signature(ALGORITHM)
        self.public_key  = self._signer.generate_keypair()
        self.private_key = self._signer.export_secret_key()
        self.address     = self._derive_address(self.public_key)

    @staticmethod
    def _derive_address(public_key: bytes) -> str:
        """
        Derive a wallet address from a public key.

        Process:
            1. Hash the public key with SHA3-256.
            2. Take the first ADDR_LENGTH hex characters.
            3. Prepend the chain prefix.

        SHA3-256 is used because it is based on the Keccak
        permutation, which has no known quantum speedup beyond
        Grover's algorithm. Grover halves the effective bit
        security, so SHA3-256 retains 128 bits of post-quantum
        preimage resistance   sufficient for address derivation.
        """
        digest = hashlib.sha3_256(public_key).hexdigest()
        return f"{ADDR_PREFIX}_{digest[:ADDR_LENGTH].upper()}"

    def sign(self, data: bytes) -> bytes:
        """
        Sign arbitrary bytes with this wallet's private key.
        Returns a raw ML-DSA-65 signature.
        """
        if not isinstance(data, bytes):
            raise WalletError("sign() requires bytes input.")
        return self._signer.sign(data)

    @staticmethod
    def verify(data: bytes, signature: bytes, public_key: bytes) -> bool:
        """
        Verify a ML-DSA-65 signature against a public key.

        This is intentionally a static method. Verification
        requires no private material   any node on the network
        can call this with only the sender's public key.

        Returns True if valid, False otherwise.
        Never raises   a malformed signature is treated as invalid.
        """
        try:
            verifier = oqs.Signature(ALGORITHM)
            return verifier.verify(data, signature, public_key)
        except Exception:
            return False

    def save(self, filepath: str) -> None:
        """
        Persist wallet keys to a JSON file using base64 encoding.

        Security note: the private key is stored in plaintext.
        In production, encrypt the private key with a
        password-derived key (e.g. Argon2 + AES-GCM)
        before writing to disk. That is out of scope here.
        """
        payload = {
            "algorithm"   : ALGORITHM,
            "address"     : self.address,
            "public_key"  : base64.b64encode(self.public_key).decode(),
            "private_key" : base64.b64encode(self.private_key).decode()
        }
        with open(filepath, "w") as f:
            json.dump(payload, f, indent=4)

    @classmethod
    def load(cls, filepath: str) -> "QuantumWallet":
        """
        Reconstruct a wallet from a saved JSON file.
        Re-initializes the liboqs signer with the stored private key.
        """
        if not os.path.exists(filepath):
            raise WalletError(f"Wallet file not found: {filepath}")

        with open(filepath, "r") as f:
            data = json.load(f)

        instance             = cls.__new__(cls)
        instance.public_key  = base64.b64decode(data["public_key"])
        instance.private_key = base64.b64decode(data["private_key"])
        instance.address     = data["address"]
        instance._signer     = oqs.Signature(ALGORITHM, instance.private_key)

        return instance

    def __repr__(self) -> str:
        return (
            f"QuantumWallet("
            f"algorithm={ALGORITHM}, "
            f"address={self.address}, "
            f"pubkey_bytes={len(self.public_key)})"
        )
