import hashlib
import json
import time


class BlockError(Exception):
    pass


class Block:
    def __init__(self, index: int, transactions: list, previous_hash: str):
        if index < 0:
            raise BlockError("Block index cannot be negative.")
        if not isinstance(previous_hash, str) or not previous_hash:
            raise BlockError("A previous hash is required.")

        self.index = index
        self.transactions = list(transactions)
        self.previous_hash = previous_hash
        self.timestamp = time.time()
        self.nonce = 0
        self.hash = self.compute_hash()

    def _core(self) -> dict:
        return {
            "index": self.index,
            "transactions": [
                {
                    "tx_id": transaction.tx_id,
                    "sender": transaction.sender,
                    "inputs": transaction.inputs,
                    "outputs": transaction.outputs,
                    "fee": transaction.fee,
                    "is_coinbase": transaction.is_coinbase,
                    "timestamp": transaction.timestamp,
                    "signature": transaction.signature.hex(),
                    "sender_public_key": transaction.sender_public_key.hex(),
                }
                for transaction in self.transactions
            ],
            "previous_hash": self.previous_hash,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
        }

    def compute_hash(self) -> str:
        payload = json.dumps(self._core(), sort_keys=True).encode("utf-8")
        return hashlib.sha3_256(payload).hexdigest()

    def recompute_hash(self) -> None:
        self.hash = self.compute_hash()

    def is_internally_valid(self) -> bool:
        return self.hash == self.compute_hash()

    def transaction_count(self) -> int:
        return len(self.transactions)

    def to_dict(self) -> dict:
        return {
            **self._core(),
            "hash": self.hash,
        }

    def __repr__(self) -> str:
        return (
            f"Block(index={self.index}, "
            f"transactions={self.transaction_count()}, "
            f"hash={self.hash[:16]}..., nonce={self.nonce})"
        )