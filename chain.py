import json

from block import Block
from transaction import Transaction


class ChainError(Exception):
    pass


class Blockchain:
    """
    An ordered, linked sequence of blocks.

    The chain enforces three invariants at all times:

        1. Linkage: every block's previous_hash equals the hash of
           the block before it. A break in linkage means a block was
           inserted, removed, or its predecessor was altered.

        2. Integrity: every block's stored hash matches a fresh
           recomputation of that block's contents. A mismatch means
           a field inside the block was modified after it was added.

        3. Transaction validity: every transaction in every block
           carries a valid ML-DSA-65 signature from its sender.
           A block containing an invalid transaction is rejected.

    These three checks together mean that once a transaction is
    confirmed in a block, it cannot be altered, removed, or forged
    without detection.

    Proof of Work:
        To add a block, the miner must find a nonce such that the
        block's hash starts with DIFFICULTY leading zeros. This makes
        block production computationally expensive, which is what
        prevents an attacker from cheaply rewriting the chain.

        DIFFICULTY = 4 means the hash must start with "0000".
        Each additional zero multiplies the expected work by 16.
        At difficulty 4, a modern CPU finds a valid nonce in roughly
        100–500ms, which is intentional for a prototype.
    """

    DIFFICULTY = 4

    def __init__(self):
        self.chain : list[Block] = []
        self._create_genesis_block()

    def _create_genesis_block(self) -> None:
        """
        Create and mine the first block.

        The genesis block has no predecessor, so its previous_hash is
        set to 64 zeros — a conventional placeholder that makes the
        hash value explicit and unambiguous rather than an empty string
        or None, which would be fragile to serialize consistently.
        """
        genesis = Block(
            index         = 0,
            transactions  = [],
            previous_hash = "0" * 64
        )
        self._mine(genesis)
        self.chain.append(genesis)

    def _mine(self, block: Block) -> None:
        """
        Increment the nonce until the block hash satisfies the target.

        The target is a hash that starts with DIFFICULTY zero characters.
        Because SHA3-256 output is uniformly distributed, the expected
        number of iterations is 16^DIFFICULTY. There is no shortcut —
        each candidate must be hashed independently.
        """
        target = "0" * self.DIFFICULTY
        while not block.hash.startswith(target):
            block.nonce += 1
            block.recompute_hash()

    def _last_block(self) -> Block:
        return self.chain[-1]

    def add_block(self, transactions: list) -> Block:
        """
        Validate a set of transactions, mine a new block, and append it.

        Validation happens before mining. There is no point spending
        compute on a block that would be rejected anyway.

        Raises ChainError if any transaction fails signature verification.
        """
        for tx in transactions:
            if not tx.is_valid():
                raise ChainError(
                    f"Block rejected: invalid transaction detected.\n"
                    f"  tx_id  : {tx.tx_id}\n"
                    f"  sender : {tx.sender}"
                )

        new_block = Block(
            index         = len(self.chain),
            transactions  = transactions,
            previous_hash = self._last_block().hash
        )

        self._mine(new_block)
        self.chain.append(new_block)
        return new_block

    def is_valid(self) -> bool:
        """
        Walk the entire chain and verify all three invariants.

        Starts at block index 1 because the genesis block has no
        predecessor to link against. The genesis block's internal
        integrity is still checked.

        Returns True only if every check passes for every block.
        Prints the specific failure before returning False so the
        caller knows exactly where the chain broke.
        """
        for i in range(1, len(self.chain)):
            current  = self.chain[i]
            previous = self.chain[i - 1]

            # Invariant 1: internal integrity
            if not current.is_internally_valid():
                print(
                    f"Integrity failure at block {i}: "
                    f"stored hash does not match block contents."
                )
                return False

            # Invariant 2: chain linkage
            if current.previous_hash != previous.hash:
                print(
                    f"Linkage failure at block {i}: "
                    f"previous_hash does not match block {i - 1} hash."
                )
                return False

            # Invariant 3: transaction signatures
            for tx in current.transactions:
                if not tx.is_valid():
                    print(
                        f"Signature failure at block {i}: "
                        f"transaction {tx.tx_id[:16]} has invalid signature."
                    )
                    return False

        return True

    def tamper(self, block_index: int, field: str, value) -> None:
        """
        Directly modify a block field without updating the hash.

        This method exists only for testing. It simulates an attacker
        who edits raw chain data. Calling is_valid() after tamper()
        should always return False.
        """
        if block_index >= len(self.chain):
            raise ChainError(f"No block at index {block_index}.")
        setattr(self.chain[block_index], field, value)

    def print_chain(self) -> None:
        for block in self.chain:
            print(repr(block))

    def to_dict(self) -> list:
        return [block.to_dict() for block in self.chain]

    def height(self) -> int:
        return len(self.chain)

    def __repr__(self) -> str:
        return f"Blockchain(height={self.height()}, valid={self.is_valid()})"