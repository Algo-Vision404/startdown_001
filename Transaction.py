import hashlib
import json
import time
import base64

from wallet import QuantumWallet


class TransactionError(Exception):
    pass


class Transaction:
    """
    A signed transfer of value between two wallet addresses.

    A transaction is considered valid if and only if:
        1. It carries a non-empty ML-DSA-65 signature.
        2. The attached public key re-derives to the sender address.
        3. The signature verifies against the serialized core fields.

    Condition 2 prevents an attacker from substituting a different
    public key to pass signature verification while spoofing the
    sender address field.

    Condition 3 prevents tampering with any field after signing,
    since altering any field changes the serialized bytes and
    invalidates the signature.
    """

    def __init__(
        self,
        sender_address    : str,
        recipient_address : str,
        amount            : float
    ):
        if amount <= 0:
            raise TransactionError("Amount must be positive.")

        self.sender            = sender_address
        self.recipient         = recipient_address
        self.amount            = amount
        self.timestamp         = time.time()

        self.tx_id             = ""
        self.signature         = b""
        self.sender_public_key = b""

    def _core(self) -> dict:
        """
        The canonical set of fields that are hashed and signed.

        sort_keys=True is critical — without it, dict serialization
        order is non-deterministic across Python versions and
        platforms, which would produce different byte strings for
        the same logical transaction.
        """
        return {
            "sender"    : self.sender,
            "recipient" : self.recipient,
            "amount"    : self.amount,
            "timestamp" : self.timestamp
        }

    def to_bytes(self) -> bytes:
        return json.dumps(self._core(), sort_keys=True).encode("utf-8")

    def _compute_tx_id(self) -> str:
        """
        SHA3-256 hash of the serialized core fields.
        Serves as the unique identifier for this transaction.
        """
        return hashlib.sha3_256(self.to_bytes()).hexdigest()

    def sign(self, wallet: QuantumWallet) -> None:
        """
        Attach a ML-DSA-65 signature to this transaction.

        Enforces that the signing wallet owns the sender address.
        This check is local only — it does not replace network-level
        address validation, which nodes perform independently during
        block verification.
        """
        if wallet.address != self.sender:
            raise TransactionError(
                f"Signing wallet address does not match sender.\n"
                f"  wallet  : {wallet.address}\n"
                f"  sender  : {self.sender}"
            )

        self.tx_id             = self._compute_tx_id()
        self.signature         = wallet.sign(self.to_bytes())
        self.sender_public_key = wallet.public_key

    def is_valid(self) -> bool:
        """
        Verify the transaction against its attached public key.

        Validation steps:
            1. Reject if signature or public key is missing.
            2. Re-derive the address from the public key.
               Reject if it does not match the sender field.
            3. Run ML-DSA-65 signature verification.

        Returns True only if all three steps pass.
        """
        if not self.signature or not self.sender_public_key:
            return False

        derived = QuantumWallet._derive_address(self.sender_public_key)
        if derived != self.sender:
            return False

        return QuantumWallet.verify(
            self.to_bytes(),
            self.signature,
            self.sender_public_key
        )

    def to_dict(self) -> dict:
        return {
            "tx_id"            : self.tx_id,
            "sender"           : self.sender,
            "recipient"        : self.recipient,
            "amount"           : self.amount,
            "timestamp"        : self.timestamp,
            "signature_bytes"  : len(self.signature),
            "valid"            : self.is_valid()
        }

    def __repr__(self) -> str:
        return (
            f"Transaction("
            f"tx_id={self.tx_id[:16]}..., "
            f"sender={self.sender[:16]}..., "
            f"amount={self.amount}, "
            f"valid={self.is_valid()})"
        )