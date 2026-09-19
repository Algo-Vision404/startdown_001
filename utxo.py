# utxo.py
#
# Unspent Transaction Output (UTXO) set.
#
# In the account model (what we had before), balance is a single
# number stored per address. Simple, but it requires scanning the
# entire chain to compute and has no concept of "which specific
# coins are available to spend."
#
# In the UTXO model (what Bitcoin uses), a transaction does not
# transfer a balance — it consumes specific previous outputs and
# creates new ones. A coin exists as a discrete output. It is either
# spent (consumed by a later transaction) or unspent (available).
#
# A UTXO is identified by:
#     (tx_id, output_index)
#
# Each UTXO has:
#     address  -- who can spend it
#     amount   -- how much it is worth
#
# The UTXO set is the complete set of all unspent outputs at the
# current chain tip. It is the only thing needed to validate a new
# transaction — you do not need to scan the full chain history.
#
# When a block is appended:
#     1. For each transaction, remove every input's referenced UTXO.
#     2. For each transaction, add each new output as a UTXO.
#
# When a block is rolled back (chain reorganization):
#     1. For each transaction, remove each output UTXO.
#     2. For each transaction, restore each input's referenced UTXO.
#
# The genesis block creates no UTXOs. Coins enter the system only
# through coinbase transactions (block rewards).


class UTXO:
    """
    A single unspent transaction output.

    Identified by the tx_id and output index of the transaction
    that created it. Owned by address. Worth amount coins.
    """

    def __init__(self, tx_id: str, index: int, address: str, amount: float):
        self.tx_id   = tx_id
        self.index   = index
        self.address = address
        self.amount  = amount

    def key(self) -> tuple:
        """Unique identifier for this output."""
        return (self.tx_id, self.index)

    def to_dict(self) -> dict:
        return {
            "tx_id"  : self.tx_id,
            "index"  : self.index,
            "address": self.address,
            "amount" : self.amount
        }

    def __repr__(self) -> str:
        return (
            f"UTXO(tx={self.tx_id[:16]}..., "
            f"idx={self.index}, "
            f"addr={self.address[:20]}..., "
            f"amount={self.amount})"
        )


class UTXOSet:
    """
    The complete set of all unspent outputs at the current chain tip.

    Internally stored as a dict:
        (tx_id, output_index) -> UTXO

    This gives O(1) lookup for both existence checks and retrieval,
    which is critical for transaction validation performance.
    """

    def __init__(self):
        self._utxos : dict[tuple, UTXO] = {}

    # ─────────────────────────────────────────────────────────
    # Core operations
    # ─────────────────────────────────────────────────────────

    def add(self, utxo: UTXO) -> None:
        """Add a new unspent output to the set."""
        self._utxos[utxo.key()] = utxo

    def spend(self, tx_id: str, index: int) -> UTXO | None:
        """
        Remove and return a UTXO, marking it as spent.
        Returns None if the output does not exist or is already spent.
        """
        return self._utxos.pop((tx_id, index), None)

    def get(self, tx_id: str, index: int) -> UTXO | None:
        """Look up a UTXO without spending it."""
        return self._utxos.get((tx_id, index))

    def exists(self, tx_id: str, index: int) -> bool:
        return (tx_id, index) in self._utxos

    # ─────────────────────────────────────────────────────────
    # Balance query
    # ─────────────────────────────────────────────────────────

    def balance(self, address: str) -> float:
        """
        Sum all unspent outputs belonging to an address.

        O(n) where n = number of UTXOs in the set.
        In production this is optimized with an address index
        (a secondary dict mapping address -> set of UTXO keys).
        For this prototype, a linear scan is sufficient.
        """
        return round(
            sum(u.amount for u in self._utxos.values() if u.address == address),
            8
        )

    def utxos_for(self, address: str) -> list[UTXO]:
        """Return all unspent outputs belonging to an address."""
        return [u for u in self._utxos.values() if u.address == address]

    # ─────────────────────────────────────────────────────────
    # Block application
    # ─────────────────────────────────────────────────────────

    def apply_block(self, block) -> None:
        """
        Update the UTXO set by applying a confirmed block.

        Order matters: inputs must be removed before outputs are added.
        If a transaction spends an output created earlier in the same
        block, removing inputs first prevents a false "already spent"
        error when the output is later looked up.

        Coinbase transactions (block rewards) have no inputs —
        they create coins from nothing. They are handled identically
        to regular transactions here: their outputs are simply added.
        """
        for tx in block.transactions:
            # Remove spent outputs
            for inp in tx.inputs:
                self.spend(inp["tx_id"], inp["index"])

            # Add new unspent outputs
            for i, out in enumerate(tx.outputs):
                self.add(UTXO(tx.tx_id, i, out["address"], out["amount"]))

    def rollback_block(self, block, previous_utxos: list) -> None:
        """
        Undo a block by reversing its UTXO changes.

        Used during chain reorganization when a longer valid fork
        is adopted and the current tip must be unwound.

        previous_utxos is a list of UTXO dicts that were spent by
        this block and must be restored. The caller is responsible
        for tracking these at apply time.

        New outputs created by this block are simply removed.
        """
        for tx in block.transactions:
            # Remove outputs this block created
            for i in range(len(tx.outputs)):
                self.spend(tx.tx_id, i)

        # Restore inputs this block consumed
        for utxo_dict in previous_utxos:
            utxo = UTXO(
                utxo_dict["tx_id"],
                utxo_dict["index"],
                utxo_dict["address"],
                utxo_dict["amount"]
            )
            self.add(utxo)

    # ─────────────────────────────────────────────────────────
    # Serialization
    # ─────────────────────────────────────────────────────────

    def to_dict(self) -> list:
        return [u.to_dict() for u in self._utxos.values()]

    @classmethod
    def from_dict(cls, data: list) -> "UTXOSet":
        utxo_set = cls()
        for entry in data:
            utxo_set.add(UTXO(
                entry["tx_id"],
                entry["index"],
                entry["address"],
                entry["amount"]
            ))
        return utxo_set

    def size(self) -> int:
        return len(self._utxos)

    def __repr__(self) -> str:
        return f"UTXOSet(size={self.size()})"