# merkle.py
#
# Merkle tree implementation for transaction sets.
#
# Properties:
#   - Leaves are SHA3-256 hashes of serialized transactions.
#   - Internal nodes are SHA3-256(left_child + right_child).
#   - If the number of leaves is odd, the last leaf is duplicated.
#     This is the same convention Bitcoin uses.
#   - The empty tree (no transactions) has a defined root of 64 zeros.
#     This handles the genesis block which contains no transactions.
#
# The Merkle root is included in the block header and therefore
# covered by the block hash. Altering any transaction changes the
# root, changes the header, changes the hash, and breaks the chain.
#
# Merkle proofs allow a light client to verify that a specific
# transaction is included in a block using only:
#   - The transaction itself
#   - A proof path (list of sibling hashes)
#   - The block header (which contains the Merkle root)
#
# Proof size is O(log n) where n is the number of transactions.
# For a block with 1024 transactions, a proof is 10 hashes = 320 bytes.
# Without Merkle proofs, verification requires the full block.

import hashlib
from typing import Optional


EMPTY_ROOT = "0" * 64


def _sha3(data: bytes) -> str:
    """SHA3-256 of raw bytes, returned as a hex string."""
    return hashlib.sha3_256(data).hexdigest()


def _hash_pair(left: str, right: str) -> str:
    """
    Hash two child nodes into a parent node.

    Concatenates the hex strings as bytes before hashing.
    This is deterministic and order-dependent — swapping left
    and right produces a different hash, which is correct.
    """
    combined = bytes.fromhex(left) + bytes.fromhex(right)
    return _sha3(combined)


def _tx_hash(tx) -> str:
    """
    Compute the leaf hash for a transaction.

    Uses the transaction's tx_id if already computed,
    otherwise recomputes from the canonical serialization.
    The tx_id is itself a SHA3-256 hash of the transaction's
    core fields, so this is SHA3-256(SHA3-256(tx_bytes)).
    Double hashing is intentional — it prevents length-extension
    attacks on the Merkle tree, following the same reasoning
    Bitcoin uses for its double SHA-256.
    """
    if tx.tx_id:
        return _sha3(bytes.fromhex(tx.tx_id))
    return _sha3(tx.to_bytes())


class MerkleTree:
    """
    A complete binary Merkle tree over a set of transactions.

    The tree is built once at construction time and is immutable.
    Internal state:
        _leaves  -- list of leaf hashes (one per transaction)
        _layers  -- list of layers from leaves to root
                    _layers[0] = leaves
                    _layers[-1] = [root]
    """

    def __init__(self, transactions: list):
        if not transactions:
            self.root   = EMPTY_ROOT
            self._leaves = []
            self._layers = []
            return

        self._leaves = [_tx_hash(tx) for tx in transactions]
        self._layers = self._build(self._leaves)
        self.root    = self._layers[-1][0]

    @staticmethod
    def _build(leaves: list) -> list:
        """
        Build all layers of the tree bottom-up.

        At each layer:
            - If the layer has one node, it is the root. Stop.
            - If the layer has an odd number of nodes, duplicate
              the last node so every pair is complete.
            - Hash each pair into the next layer.

        Returns a list of layers, from leaves to root.
        """
        layers = [leaves[:]]
        current = leaves[:]

        while len(current) > 1:
            # Duplicate last element if odd number of nodes
            if len(current) % 2 == 1:
                current.append(current[-1])

            next_layer = []
            for i in range(0, len(current), 2):
                parent = _hash_pair(current[i], current[i + 1])
                next_layer.append(parent)

            layers.append(next_layer)
            current = next_layer

        return layers

    # ─────────────────────────────────────────────────────────
    # Proof generation
    # ─────────────────────────────────────────────────────────

    def proof(self, tx_index: int) -> Optional[list]:
        """
        Generate a Merkle proof for the transaction at tx_index.

        Returns a list of proof steps, each a dict:
            {
                "hash"     : str   -- the sibling hash
                "position" : str   -- "left" or "right"
                                      (position of the sibling
                                       relative to the current node)
            }

        Returns None if tx_index is out of range or the tree is empty.

        To verify the proof:
            Start with the transaction hash.
            For each step:
                if position == "left":
                    current = hash_pair(step.hash, current)
                if position == "right":
                    current = hash_pair(current, step.hash)
            The final current must equal the root.
        """
        if not self._layers or tx_index >= len(self._leaves):
            return None

        proof_steps = []
        index       = tx_index

        for layer in self._layers[:-1]:
            # Ensure the layer has an even number of elements
            # (same duplication logic as in _build)
            if len(layer) % 2 == 1:
                layer = layer + [layer[-1]]

            if index % 2 == 0:
                # Current node is a left child.
                # Sibling is to the right.
                sibling_index = index + 1
                position      = "right"
            else:
                # Current node is a right child.
                # Sibling is to the left.
                sibling_index = index - 1
                position      = "left"

            proof_steps.append({
                "hash"     : layer[sibling_index],
                "position" : position
            })

            # Move to the parent index in the next layer
            index = index // 2

        return proof_steps

    # ─────────────────────────────────────────────────────────
    # Proof verification — static, no tree needed
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def verify_proof(
        tx_hash    : str,
        proof      : list,
        merkle_root: str
    ) -> bool:
        """
        Verify a Merkle proof.

        Parameters:
            tx_hash     -- SHA3-256 hash of the transaction being proved
            proof       -- list of proof steps from MerkleTree.proof()
            merkle_root -- the Merkle root from the block header

        Returns True if the proof is valid, False otherwise.

        This method is static because a light client can verify
        a proof without building or storing the full tree.
        It only needs the transaction, the proof path, and the root.
        """
        current = tx_hash

        for step in proof:
            sibling  = step["hash"]
            position = step["position"]

            if position == "right":
                # Sibling is to the right: current is left child
                current = _hash_pair(current, sibling)
            elif position == "left":
                # Sibling is to the left: current is right child
                current = _hash_pair(sibling, current)
            else:
                return False

        return current == merkle_root

    # ─────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────

    def leaf_hash(self, tx_index: int) -> Optional[str]:
        """Return the leaf hash for a transaction by index."""
        if tx_index >= len(self._leaves):
            return None
        return self._leaves[tx_index]

    def depth(self) -> int:
        """Number of layers including the leaf layer."""
        return len(self._layers)

    def size(self) -> int:
        """Number of transactions (leaves) in the tree."""
        return len(self._leaves)

    def to_dict(self) -> dict:
        """Serializable representation for debugging and the API."""
        return {
            "root"   : self.root,
            "size"   : self.size(),
            "depth"  : self.depth(),
            "leaves" : self._leaves
        }

    def __repr__(self) -> str:
        return (
            f"MerkleTree("
            f"root={self.root[:16]}..., "
            f"size={self.size()}, "
            f"depth={self.depth()})"
        )


# ─────────────────────────────────────────────────────────────
# Module-level convenience function
# ─────────────────────────────────────────────────────────────

def merkle_root(transactions: list) -> str:
    """
    Compute the Merkle root for a list of transactions.
    Convenience wrapper for use in block.py.
    """
    return MerkleTree(transactions).root