# transaction.py
#
# A UTXO-based signed transaction.
#
# Structure change from the account model:
#
#   Before:
#       sender, recipient, amount
#       (implicit: sender's full balance is available to spend)
#
#   Now:
#       inputs  : list of {tx_id, index} references to UTXOs being spent
#       outputs : list of {address, amount} new UTXOs being created
#       fee     : inputs_total - outputs_total, paid to the miner
#
# Validation rules:
#   1. ML-DSA-65 signature is valid.
#   2. At least one input and one output.
#   3. No duplicate inputs (double-spend within the transaction).
#   4. All inputs exist in the current UTXO set.
#   5. All inputs are owned by the sender address.
#   6. sum(inputs) >= sum(outputs)   (cannot create coins from nothing)
#   7. fee >= 0
#
# Coinbase transactions (block rewards) are exempt from rules 2-6
# because they have no inputs and create coins from nothing.
# They are identified by the is_coinbase flag.

import hashlib
import json
import time
import base64
import math

from wallet import QuantumWallet, ALGORITHM


class TransactionError(Exception):
    pass


class Transaction:
    """
    A UTXO-based transaction with ML-DSA-65 signature.
    """

    COINBASE_SENDER = "COINBASE"

    def __init__(
        self,
        sender_address : str,
        inputs         : list,
        outputs        : list,
        fee            : float = 0.0,
        is_coinbase    : bool  = False
    ):
        """
        inputs  : [{"tx_id": str, "index": int}, ...]
        outputs : [{"address": str, "amount": float}, ...]
        fee     : amount paid to the miner (inputs_total - outputs_total)
        """
        if not is_coinbase and fee < 0:
            raise TransactionError("fee cannot be negative")

        self.sender        = sender_address
        self.inputs        = inputs
        self.outputs       = outputs
        self.fee           = fee
        self.is_coinbase   = is_coinbase
        self.timestamp     = time.time()

        self.tx_id             = ""
        self.signature         = b""
        self.sender_public_key = b""

    # ─────────────────────────────────────────────────────────
    # Convenience constructors
    # ─────────────────────────────────────────────────────────

    @classmethod
    def coinbase(cls, miner_address: str, reward: float) -> "Transaction":
        """
        Create a coinbase transaction that mints the block reward.

        Coinbase transactions have no inputs. They create coins
        from nothing, authorized by the protocol rules rather than
        by a private key signature.

        The miner's address receives the full reward as a single output.
        In practice the reward also includes all fees from the block's
        transactions, but that aggregation is done in chain.py.
        """
        tx = cls(
            sender_address = cls.COINBASE_SENDER,
            inputs         = [],
            outputs        = [{"address": miner_address, "amount": reward}],
            fee            = 0.0,
            is_coinbase    = True
        )
        tx.tx_id = tx._compute_tx_id()
        return tx

    @classmethod
    def transfer(
        cls,
        sender_wallet  : QuantumWallet,
        utxo_set,
        recipient_address : str,
        amount            : float,
        fee               : float = 0.0
    ) -> "Transaction":
        """
        Build and sign a transfer transaction automatically.

        Selects UTXOs from the sender's unspent outputs (largest first)
        until the required amount + fee is covered. Creates a change
        output back to the sender if the selected inputs exceed the
        required total.

        This is the standard "coin selection" algorithm. Largest-first
        minimizes the number of inputs, keeping transaction size down.

        Raises TransactionError if the sender has insufficient funds.
        """
        required = amount + fee
        available = utxo_set.utxos_for(sender_wallet.address)

        if not available:
            raise TransactionError(
                f"no UTXOs found for {sender_wallet.address[:24]}..."
            )

        # Sort largest first to minimize input count
        available.sort(key=lambda u: u.amount, reverse=True)

        selected  = []
        total_in  = 0.0

        for utxo in available:
            selected.append(utxo)
            total_in += utxo.amount
            if total_in >= required:
                break

        if total_in < required:
            raise TransactionError(
                f"insufficient funds: "
                f"have {round(total_in, 8)}, need {required}"
            )

        inputs = [{"tx_id": u.tx_id, "index": u.index} for u in selected]

        outputs = [{"address": recipient_address, "amount": amount}]

        change = round(total_in - amount - fee, 8)
        if change > 0:
            outputs.append({"address": sender_wallet.address, "amount": change})

        tx = cls(
            sender_address = sender_wallet.address,
            inputs         = inputs,
            outputs        = outputs,
            fee            = fee
        )
        tx.sign(sender_wallet)
        return tx

    # ─────────────────────────────────────────────────────────
    # Serialization
    # ─────────────────────────────────────────────────────────

    def _core(self) -> dict:
        """
        The canonical fields that are hashed and signed.
        Inputs and outputs are included so neither can be altered
        after signing without invalidating the signature.
        """
        return {
            "sender"     : self.sender,
            "inputs"     : self.inputs,
            "outputs"    : self.outputs,
            "fee"        : self.fee,
            "is_coinbase": self.is_coinbase,
            "timestamp"  : self.timestamp
        }

    def to_bytes(self) -> bytes:
        return json.dumps(self._core(), sort_keys=True).encode("utf-8")

    def _compute_tx_id(self) -> str:
        return hashlib.sha3_256(self.to_bytes()).hexdigest()

    # ─────────────────────────────────────────────────────────
    # Signing
    # ─────────────────────────────────────────────────────────

    def sign(self, wallet: QuantumWallet) -> None:
        if wallet.address != self.sender:
            raise TransactionError(
                f"signing wallet does not match sender\n"
                f"  wallet : {wallet.address}\n"
                f"  sender : {self.sender}"
            )

        self.tx_id             = self._compute_tx_id()
        self.signature         = wallet.sign(self.to_bytes())
        self.sender_public_key = wallet.public_key

    # ─────────────────────────────────────────────────────────
    # Validation
    # ─────────────────────────────────────────────────────────

    def is_valid(self) -> bool:
        """
        Validate signature only.
        UTXO existence and balance checks are done by the chain.
        """
        if self.tx_id != self._compute_tx_id():
            return False

        if self.is_coinbase:
            return True

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

    def validate_against_utxo_set(self, utxo_set) -> bool:
        """
        Full UTXO validation.

        Checks:
            1. At least one input and one output.
            2. No duplicate inputs.
            3. All inputs exist in the UTXO set.
            4. All inputs owned by sender.
            5. Input total >= output total.
            6. Signature valid.
        """
        if self.is_coinbase:
            return True

        if not self.inputs or not self.outputs:
            return False

        if (
            isinstance(self.fee, bool)
            or not isinstance(self.fee, (int, float))
            or not math.isfinite(self.fee)
            or self.fee < 0
        ):
            return False

        for output in self.outputs:
            amount = output.get("amount")
            if (
                isinstance(amount, bool)
                or not isinstance(amount, (int, float))
                or not math.isfinite(amount)
                or amount <= 0
            ):
                return False

        # No duplicate inputs
        input_keys = [(i["tx_id"], i["index"]) for i in self.inputs]
        if len(input_keys) != len(set(input_keys)):
            return False

        input_total = 0.0
        for inp in self.inputs:
            utxo = utxo_set.get(inp["tx_id"], inp["index"])
            if utxo is None:
                return False
            if utxo.address != self.sender:
                return False
            input_total += utxo.amount

        output_total = sum(o["amount"] for o in self.outputs)

        if input_total < output_total:
            return False

        # Fees are the exact remainder after outputs. Allowing the declared
        # fee to differ would let a block miner mint arbitrary extra coins.
        expected_fee = round(input_total - output_total, 8)
        if self.fee < 0 or abs(self.fee - expected_fee) > 1e-8:
            return False

        return self.is_valid()

    # ─────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────

    def total_output(self) -> float:
        return sum(o["amount"] for o in self.outputs)

    def to_dict(self) -> dict:
        return {
            "tx_id"      : self.tx_id,
            "sender"     : self.sender,
            "inputs"     : self.inputs,
            "outputs"    : self.outputs,
            "fee"        : self.fee,
            "is_coinbase": self.is_coinbase,
            "timestamp"  : self.timestamp,
            "sig_bytes"  : len(self.signature),
            "valid"      : self.is_valid()
        }

    def __repr__(self) -> str:
        return (
            f"Transaction("
            f"tx_id={self.tx_id[:16]}..., "
            f"coinbase={self.is_coinbase}, "
            f"fee={self.fee}, "
            f"outputs={len(self.outputs)})"
        )