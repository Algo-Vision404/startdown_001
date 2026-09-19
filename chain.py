# chain.py
#
# Blockchain with UTXO set, block rewards, and fee collection.

import json

from block import Block
from Transaction import Transaction
from utxo import UTXOSet, UTXO


class ChainError(Exception):
    pass


class Blockchain:

    DIFFICULTY    = 4
    BLOCK_REWARD  = 50.0     # coins awarded to the miner per block
    HALVING       = 210000   # halve the reward every N blocks (like Bitcoin)

    def __init__(self):
        self.chain    : list[Block] = []
        self.utxo_set : UTXOSet     = UTXOSet()
        self._create_genesis_block()

    # ─────────────────────────────────────────────────────────
    # Block reward
    # ─────────────────────────────────────────────────────────

    def block_reward(self, height: int) -> float:
        """
        Compute the block reward at a given height.

        The reward halves every HALVING blocks, asymptotically
        approaching zero. This is how total supply is capped.

        At height 0: 50.0
        At height 210000: 25.0
        At height 420000: 12.5
        ... and so on.
        """
        halvings = height // self.HALVING
        if halvings >= 64:
            return 0.0
        return round(self.BLOCK_REWARD / (2 ** halvings), 8)

    # ─────────────────────────────────────────────────────────
    # Genesis
    # ─────────────────────────────────────────────────────────

    def _create_genesis_block(self) -> None:
        """
        The genesis block carries no transactions and creates no UTXOs.
        Coins enter the system only through coinbase transactions in
        blocks 1 and beyond.
        """
        genesis = Block(
            index         = 0,
            transactions  = [],
            previous_hash = "0" * 64
        )
        self._mine(genesis)
        self.chain.append(genesis)

    # ─────────────────────────────────────────────────────────
    # Transaction sequence validation
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def validate_transaction_sequence(transactions: list, utxo_set: UTXOSet) -> bool:
        """
        Validate a list of transactions as they would apply IN ORDER to a
        single block, without mutating the real UTXO set.

        This exists because validating each transaction independently
        against a static UTXO set snapshot is NOT sufficient: two
        transactions in the same block could both reference the same
        unspent input, and both would pass an independent check since
        neither one has "spent" it yet from the snapshot's point of view.
        That is a double-spend within a single block, and it lets a
        transaction's outputs get created without a real backing input.

        This method tracks, locally to this call only:
            - which (tx_id, index) keys have already been consumed by an
              earlier transaction in this same sequence
            - which outputs were newly created by an earlier transaction
              in this same sequence (so legitimate same-block chained
              transactions, e.g. spending a coinbase output right away,
              still work)

        Returns True only if every transaction in the sequence is valid
        and no two transactions conflict on the same input.
        """
        locally_spent = set()   # (tx_id, index) consumed earlier in this sequence
        locally_added = {}      # (tx_id, index) -> UTXO created earlier in this sequence

        for tx in transactions:
            if tx.is_coinbase:
                for i, out in enumerate(tx.outputs):
                    locally_added[(tx.tx_id, i)] = UTXO(
                        tx.tx_id, i, out["address"], out["amount"]
                    )
                continue

            if not tx.inputs or not tx.outputs:
                return False

            input_keys = [(i["tx_id"], i["index"]) for i in tx.inputs]

            # No duplicate inputs within the transaction itself
            if len(input_keys) != len(set(input_keys)):
                return False

            input_total = 0.0
            for key in input_keys:
                # Double-spend against an earlier transaction in this block
                if key in locally_spent:
                    return False

                if key in locally_added:
                    utxo = locally_added[key]
                else:
                    utxo = utxo_set.get(*key)

                if utxo is None or utxo.address != tx.sender:
                    return False

                input_total += utxo.amount

            output_total = sum(o["amount"] for o in tx.outputs)
            if input_total < output_total:
                return False

            if not tx.is_valid():
                return False

            for key in input_keys:
                locally_spent.add(key)
                locally_added.pop(key, None)

            for i, out in enumerate(tx.outputs):
                locally_added[(tx.tx_id, i)] = UTXO(
                    tx.tx_id, i, out["address"], out["amount"]
                )

        return True

    # ─────────────────────────────────────────────────────────
    # Mining
    # ─────────────────────────────────────────────────────────

    def _mine(self, block: Block) -> None:
        target = "0" * self.DIFFICULTY
        while not block.hash.startswith(target):
            block.nonce += 1
            block.recompute_hash()

    def mine_block(
        self,
        transactions   : list,
        miner_address  : str
    ) -> Block:
        """
        Build a coinbase transaction, prepend it to the transaction
        list, validate all transactions against the UTXO set,
        then mine the block.

        The coinbase reward equals the protocol reward plus all fees
        from the included transactions.

        The coinbase is always the first transaction in the block.
        This is a protocol convention that every node relies on when
        parsing blocks — block.transactions[0] is always the coinbase.
        """
        # Validate the whole batch together (catches double-spends across
        # transactions in this same block, not just against confirmed UTXOs)
        if not self.validate_transaction_sequence(transactions, self.utxo_set):
            raise ChainError(
                "invalid transaction set: a transaction failed validation "
                "or two transactions conflict on the same input (double-spend)"
            )

        # Compute total fees
        total_fees = sum(tx.fee for tx in transactions)

        # Compute miner reward
        height = len(self.chain)
        reward = round(self.block_reward(height) + total_fees, 8)

        # Build coinbase
        coinbase = Transaction.coinbase(miner_address, reward)

        # Coinbase is always first
        all_transactions = [coinbase] + transactions

        block = Block(
            index         = len(self.chain),
            transactions  = all_transactions,
            previous_hash = self.chain[-1].hash
        )

        self._mine(block)
        return block

    def append_block(self, block: Block) -> None:
        """
        Append a pre-validated block and update the UTXO set.
        Called both after local mining and when accepting a peer block.
        """
        self.chain.append(block)
        self.utxo_set.apply_block(block)

    def add_block(self, transactions: list, miner_address: str) -> Block:
        """Mine and append in one call. Used in single-node contexts."""
        block = self.mine_block(transactions, miner_address)
        self.append_block(block)
        return block

    # ─────────────────────────────────────────────────────────
    # Validation
    # ─────────────────────────────────────────────────────────

    def is_valid(self) -> bool:
        """
        Rebuild the UTXO set from genesis and verify the full chain.

        This is a complete re-validation. It is expensive — O(n) in
        the number of transactions in the chain. In production, this
        is only called on startup and during chain reorganization.
        Real-time validation uses the incremental UTXO updates in
        append_block() instead.
        """
        replay_utxo = UTXOSet()

        for i in range(1, len(self.chain)):
            current  = self.chain[i]
            previous = self.chain[i - 1]

            if not current.is_internally_valid():
                print(f"integrity failure at block {i}")
                return False

            if current.previous_hash != previous.hash:
                print(f"linkage failure at block {i}")
                return False

            if not current.hash.startswith("0" * self.DIFFICULTY):
                print(f"proof-of-work failure at block {i}")
                return False

            # First transaction must be coinbase
            if not current.transactions or not current.transactions[0].is_coinbase:
                print(f"missing coinbase at block {i}")
                return False

            for j, tx in enumerate(current.transactions):
                if j == 0:
                    # Coinbase: apply outputs, skip input validation
                    for k, out in enumerate(tx.outputs):
                        replay_utxo.add(UTXO(tx.tx_id, k, out["address"], out["amount"]))
                    continue

                if not tx.validate_against_utxo_set(replay_utxo):
                    print(f"transaction validation failure at block {i} tx {j}")
                    return False

                # Apply transaction to replay set
                for inp in tx.inputs:
                    replay_utxo.spend(inp["tx_id"], inp["index"])
                for k, out in enumerate(tx.outputs):
                    replay_utxo.add(UTXO(tx.tx_id, k, out["address"], out["amount"]))

        return True

    # ─────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────

    def _last_block(self) -> Block:
        return self.chain[-1]

    def height(self) -> int:
        return len(self.chain)

    def print_chain(self) -> None:
        for block in self.chain:
            print(repr(block))

    def to_dict(self) -> list:
        return [block.to_dict() for block in self.chain]

    def tamper(self, block_index: int, field: str, value) -> None:
        if block_index >= len(self.chain):
            raise ChainError(f"no block at index {block_index}")
        setattr(self.chain[block_index], field, value)

    def __repr__(self) -> str:
        return (
            f"Blockchain("
            f"height={self.height()}, "
            f"utxos={self.utxo_set.size()})"
        )