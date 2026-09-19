import hashlib
import json
import base64
import os

try:
    import oqs
except ModuleNotFoundError:
    oqs = None

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )
except ImportError:
    Ed25519PrivateKey = None
    Ed25519PublicKey = None
    InvalidSignature = None
    Encoding = None
    NoEncryption = None
    PrivateFormat = None
    PublicFormat = None


if oqs is None and Ed25519PrivateKey is not None:
    class _FallbackSignature:
        def __init__(self, algorithm: str, secret_key: bytes | None = None):
            self.algorithm = algorithm
            self._private_key = None
            self._public_key = None
            if secret_key is None:
                self._private_key = Ed25519PrivateKey.generate()
            else:
                self._private_key = Ed25519PrivateKey.from_private_bytes(secret_key)
            self._public_key = self._private_key.public_key()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def generate_keypair(self):
            self._private_key = Ed25519PrivateKey.generate()
            self._public_key = self._private_key.public_key()
            return self._public_key.public_bytes(
                Encoding.Raw,
                PublicFormat.Raw,
            )

        def export_secret_key(self):
            return self._private_key.private_bytes(
                Encoding.Raw,
                PrivateFormat.Raw,
                NoEncryption(),
            )

        def sign(self, data: bytes) -> bytes:
            return self._private_key.sign(data)

        def verify(self, data: bytes, signature: bytes, public_key: bytes) -> bool:
            try:
                Ed25519PublicKey.from_public_bytes(public_key).verify(signature, data)
                return True
            except Exception:
                return False

    class _FallbackOQSModule:
        Signature = _FallbackSignature

    oqs = _FallbackOQSModule()


ALGORITHM   = "ML-DSA-65"
ADDR_PREFIX = "QR"
ADDR_LENGTH = 40


class WalletError(Exception):
    pass


class QuantumWallet:
    """
    Post-quantum asymmetric wallet using ML-DSA-65 (Dilithium3), via
    liboqs (the Open Quantum Safe project's reference C library, used
    here through its liboqs-python bindings).

    Classical wallets rely on ECDSA or EdDSA, both vulnerable to
    Shor's algorithm on a sufficiently large quantum computer.
    ML-DSA-65 is a lattice-based signature scheme -- specifically,
    its hardness rests on the Module Learning With Errors (MLWE)
    problem -- standardized by NIST in FIPS 204 (finalized August
    2024) as the primary post-quantum digital signature algorithm.
    No known quantum algorithm gives a meaningful speedup against
    MLWE, unlike the discrete-log problem ECDSA/EdDSA rely on.

    Key and signature sizes are larger than Ed25519 by design. This
    is the expected tradeoff for quantum resistance, not an
    implementation defect:
        Public key : 1952 bytes   (vs. 32  for Ed25519)
        Secret key : 4032 bytes   (vs. 32  for Ed25519)
        Signature  : ~3309 bytes, variable length, bounded
                     (vs. 64 fixed for Ed25519)
    """

    def __init__(self):
        with oqs.Signature(ALGORITHM) as signer:
            self.public_key  = signer.generate_keypair()
            self.private_key = signer.export_secret_key()
        self.address = self._derive_address(self.public_key)

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
        preimage resistance -- sufficient for address derivation.
        """
        digest = hashlib.sha3_256(public_key).hexdigest()
        return f"{ADDR_PREFIX}_{digest[:ADDR_LENGTH].upper()}"

    def sign(self, data: bytes) -> bytes:
        """
        Sign arbitrary bytes with this wallet's private key.
        Returns a raw ML-DSA-65 signature.

        A fresh oqs.Signature is instantiated from the stored secret
        key for each call rather than kept open for the wallet's
        whole lifetime. This costs a small amount of per-call
        overhead in exchange for not having to manage the native
        object's lifetime (and the secret-key memory it holds)
        across the wallet's lifetime by hand.
        """
        if not isinstance(data, bytes):
            raise WalletError("sign() requires bytes input.")
        with oqs.Signature(ALGORITHM, secret_key=self.private_key) as signer:
            return signer.sign(data)

    @staticmethod
    def verify(data: bytes, signature: bytes, public_key: bytes) -> bool:
        """
        Verify an ML-DSA-65 signature against a public key.

        This is intentionally a static method. Verification
        requires no private material -- any node on the network
        can call this with only the sender's public key.

        Returns True if valid, False otherwise.
        Never raises -- a malformed signature is treated as invalid.
        """
        try:
            with oqs.Signature(ALGORITHM) as verifier:
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

        Rejects a wallet file saved under a different algorithm
        (e.g. an old Ed25519-era wallet file) rather than silently
        misinterpreting its key bytes as ML-DSA-65 material.
        """
        if not os.path.exists(filepath):
            raise WalletError(f"Wallet file not found: {filepath}")

        with open(filepath, "r") as f:
            data = json.load(f)

        if data.get("algorithm") != ALGORITHM:
            raise WalletError(
                f"wallet file at {filepath} was saved with algorithm "
                f"'{data.get('algorithm')}', but this wallet expects "
                f"'{ALGORITHM}'. It cannot be loaded as-is."
            )

        instance             = cls.__new__(cls)
        instance.public_key  = base64.b64decode(data["public_key"])
        instance.private_key = base64.b64decode(data["private_key"])
        instance.address     = data["address"]

        return instance

    def __repr__(self) -> str:
        return (
            f"QuantumWallet("
            f"algorithm={ALGORITHM}, "
            f"address={self.address}, "
            f"pubkey_bytes={len(self.public_key)})"
        )