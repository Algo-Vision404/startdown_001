# block.py
#
# Block structure updated to include the Merkle root in the header.
#
# The block hash now covers:
#     index, timestamp, merkle_root, previous_hash, difficulty, nonce
#
# The transactions themselves are NOT directly included in the hash.
# Instead, they are committed to via the Merkle root.
# This matches the Bitcoin block header structure.
#
# Benefits:
#   1. Block header is a fixed small size regardless of transaction count.
#   2. The Merkle root commits to all transactions with the same
#      tamper-evidence as before.
#   3. Light clients can verify the header chain without downloading
#      full blocks.
#   4. Merkle proofs allow per-transaction verification.

import hashlib
import json
import time

from Transaction import Transaction
from merkle import MerkleTree, merkle_root, EMPTY_ROOT


class BlockError(Exception):
    pass


class Block:
    """
    A single block in the quantum chain.

    Header fields (covered by the block hash):
        index           -- position in the chain (0 = genesis)
        timestamp       -- unix time at creation
        merkle_root     -- root of the Merkle tree over transactions
        previous_hash   -- hash of the preceding block
        difficulty      -- required leading-zero hex digits for this
                            block's hash, set by the chain's retarget
                            logic (see Blockchain.expected_difficulty)
        nonce           -- incremented during proof of work

    Body (not directly hashed, committed via merkle_root):
        transactions    -- list of Transaction objects

    The separation of header and body mirrors Bitcoin's design.
    The header is 168 bytes (fixed). The body is variable length.
    A light client downloads only headers to follow the chain.
    """

    def __init__(
        self,
        index         : int,
        transactions  : list,
        previous_hash : str,
        difficulty    : int,
        nonce         : int = 0
    ):
        self.index         = index
        self.timestamp     = time.time()
        self.transactions  = transactions
        self.previous_hash = previous_hash
        self.difficulty    = difficulty
        self.nonce         = nonce

        # Build the Merkle tree and store the root in the header
        self._merkle_tree  = MerkleTree(transactions)
        self.merkle_root   = self._merkle_tree.root

        # Compute the block hash over the header fields
        self.hash          = self._compute_hash()

    def _compute_hash(self) -> str:
        """
        Hash the block header.

        Only header fields are hashed — not the transactions directly.
        The transactions are committed via the merkle_root field.

        This means:
            - The hash is O(1) in transaction count.
            - Changing any transaction changes the merkle_root,
              which changes the hash.
            - The tamper-evidence is preserved.
        """
        header = {
            "index"         : self.index,
            "timestamp"     : self.timestamp,
            "merkle_root"   : self.merkle_root,
            "previous_hash" : self.previous_hash,
            "difficulty"    : self.difficulty,
            "nonce"         : self.nonce
        }
        raw = json.dumps(header, sort_keys=True).encode("utf-8")
        return hashlib.sha3_256(raw).hexdigest()

    def recompute_hash(self) -> None:
        """Recompute and store the hash. Called during mining."""
        self.hash = self._compute_hash()

    def is_internally_valid(self) -> bool:
        """
        Verify the block's internal consistency.

        Two checks:
            1. The stored hash matches the recomputed header hash.
            2. The stored merkle_root matches the recomputed
               Merkle root over the current transaction list.

        Check 2 catches an attack where someone swaps a transaction
        in the body without updating the merkle_root in the header.
        Check 1 alone would not catch this because the hash only
        covers the header, not the body directly.
        """
        if self.hash != self._compute_hash():
            return False

        recomputed_root = merkle_root(self.transactions)
        if self.merkle_root != recomputed_root:
            return False

        return True

    # ─────────────────────────────────────────────────────────
    # Merkle proof interface
    # ─────────────────────────────────────────────────────────

    def merkle_proof(self, tx_index: int) -> dict | None:
        """
        Generate a Merkle proof for the transaction at tx_index.

        Returns a dict containing:
            tx_hash     -- the leaf hash of the transaction
            proof       -- the proof path
            merkle_root -- the root (from the block header)
            block_index -- which block this proof is for
            tx_index    -- which transaction within the block

        Returns None if tx_index is out of range.

        A light client can use this to verify the transaction
        is included in this block without downloading the full block.
        """
        proof = self._merkle_tree.proof(tx_index)
        if proof is None:
            return None

        leaf_hash = self._merkle_tree.leaf_hash(tx_index)

        return {
            "block_index" : self.index,
            "block_hash"  : self.hash,
            "tx_index"    : tx_index,
            "tx_hash"     : leaf_hash,
            "merkle_root" : self.merkle_root,
            "proof"       : proof
        }

    def verify_tx_inclusion(self, tx_index: int) -> bool:
        """
        Verify that the transaction at tx_index is correctly
        included in this block via its Merkle proof.
        """
        proof_data = self.merkle_proof(tx_index)
        if not proof_data:
            return False

        return MerkleTree.verify_proof(
            tx_hash     = proof_data["tx_hash"],
            proof       = proof_data["proof"],
            merkle_root = self.merkle_root
        )

    # ─────────────────────────────────────────────────────────
    # Serialization
    # ─────────────────────────────────────────────────────────

    def _serialize_transactions(self) -> list:
        """
        Serialize transactions for storage and network transmission.
        Used by to_dict() and by message.py.
        """
        return [
            {
                "tx_id"     : tx.tx_id,
                "sender"    : tx.sender,
                "inputs"    : tx.inputs,
                "outputs"   : tx.outputs,
                "fee"       : tx.fee,
                "is_coinbase": tx.is_coinbase,
                "timestamp" : tx.timestamp
            }
            for tx in self.transactions
        ]

    def transaction_count(self) -> int:
        return len(self.transactions)

    def to_dict(self) -> dict:
        return {
            "index"         : self.index,
            "timestamp"     : self.timestamp,
            "merkle_root"   : self.merkle_root,
            "previous_hash" : self.previous_hash,
            "hash"          : self.hash,
            "difficulty"    : self.difficulty,
            "nonce"         : self.nonce,
            "transactions"  : self._serialize_transactions()
        }

    def header(self) -> dict:
        """
        Return only the block header fields.
        This is what a light client downloads and stores.
        """
        return {
            "index"         : self.index,
            "timestamp"     : self.timestamp,
            "merkle_root"   : self.merkle_root,
            "previous_hash" : self.previous_hash,
            "hash"          : self.hash,
            "difficulty"    : self.difficulty,
            "nonce"         : self.nonce,
            "tx_count"      : self.transaction_count()
        }

    def __repr__(self) -> str:
        return (
            f"Block("
            f"index={self.index}, "
            f"txs={self.transaction_count()}, "
            f"merkle={self.merkle_root[:16]}..., "
            f"hash={self.hash[:16]}..., "
            f"prev={self.previous_hash[:16]}...)"
        )