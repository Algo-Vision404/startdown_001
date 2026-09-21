# chain.py
#
# Blockchain with UTXO set, block rewards, and fee collection.

import json
import math
import time

from block import Block
from Transaction import Transaction
from utxo import UTXOSet, UTXO


class ChainError(Exception):
    pass


class Blockchain:

    BLOCK_REWARD  = 50.0     # coins awarded to the miner per block
    HALVING       = 210000   # halve the reward every N blocks (like Bitcoin)
    GENESIS_NONCE = 14002
    GENESIS_HASH  = "00009598a2a165ff16037a517046057030575475c7a64dc380a0bb3577d8617a"

    # ─────────────────────────────────────────────────────────
    # Difficulty retargeting
    # ─────────────────────────────────────────────────────────
    #
    # Difficulty is the number of required leading hex-zero digits in a
    # block's hash. Every RETARGET_INTERVAL blocks, the chain compares
    # the actual time taken to mine the last window of blocks against
    # the target, and nudges difficulty by one level (each level is a
    # 16x change in expected mining work, since it's a hex digit) if
    # blocks were mined more than 2x too fast or too slow. This mirrors
    # Bitcoin's retargeting in spirit, simplified to single-step moves
    # so difficulty can't swing wildly from one retarget to the next.
    INITIAL_DIFFICULTY = 4
    MIN_DIFFICULTY      = 1
    MAX_DIFFICULTY      = 8
    TARGET_BLOCK_TIME   = 30     # seconds, desired average time per block
    RETARGET_INTERVAL   = 10     # blocks between difficulty adjustments

    # A block's timestamp may not sit further ahead of the wall clock,
    # at validation time, than this many seconds. Mirrors Bitcoin's
    # 2-hour future-drift tolerance: real nodes' clocks are never
    # perfectly synchronized, so some slack is needed, but a block
    # dated arbitrarily far into the future should never be accepted.
    MAX_FUTURE_DRIFT_SEC = 7200

    # A block's timestamp must exceed the median of this many
    # immediately preceding blocks (median-time-past, as in Bitcoin),
    # rather than simply exceeding its immediate parent's timestamp.
    # See _median_time_past() for why this matters.
    MEDIAN_TIME_SPAN = 11

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
            previous_hash = "0" * 64,
            difficulty    = self.INITIAL_DIFFICULTY,
            nonce         = self.GENESIS_NONCE
        )
        genesis.timestamp = 0.0
        genesis.recompute_hash()
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

        for position, tx in enumerate(transactions):
            if tx.is_coinbase:
                if position != 0:
                    return False
                for i, out in enumerate(tx.outputs):
                    locally_added[(tx.tx_id, i)] = UTXO(
                        tx.tx_id, i, out["address"], out["amount"]
                    )
                continue

            if not tx.inputs or not tx.outputs:
                return False

            if (
                isinstance(tx.fee, bool)
                or not isinstance(tx.fee, (int, float))
                or not math.isfinite(tx.fee)
                or tx.fee < 0
            ):
                return False

            for output in tx.outputs:
                amount = output.get("amount")
                if (
                    isinstance(amount, bool)
                    or not isinstance(amount, (int, float))
                    or not math.isfinite(amount)
                    or amount <= 0
                ):
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

            expected_fee = round(input_total - output_total, 8)
            if tx.fee < 0 or abs(tx.fee - expected_fee) > 1e-8:
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
    # Difficulty
    # ─────────────────────────────────────────────────────────

    def expected_difficulty(self, chain: list = None) -> int:
        """
        Compute the difficulty required for the next block appended
        after `chain` (defaults to this instance's current chain).

        Accepting an explicit chain (rather than always reading
        self.chain) lets both mine_block() and is_valid() share this
        logic: mine_block() calls it with the live chain, is_valid()
        calls it with self.chain[:i] while replaying block i so it is
        re-deriving what the difficulty *should* have been at that
        point, not trusting the stored value.

        Between retargets, difficulty holds steady at the last block's
        value. At a retarget boundary, it looks at the time actually
        taken to mine the last RETARGET_INTERVAL blocks and moves by
        at most one level: up if that window ran more than 2x faster
        than target (chain is under-secured relative to its miners),
        down if it ran more than 2x slower (blocks are taking too
        long), clamped to [MIN_DIFFICULTY, MAX_DIFFICULTY].

        The genesis block is never used as a timing reference (see the
        comment above where the window is computed) — its timestamp is
        a fixed constant, not real wall-clock time.
        """
        chain = self.chain if chain is None else chain
        height = len(chain)

        if height == 0:
            return self.INITIAL_DIFFICULTY

        if height < self.RETARGET_INTERVAL or height % self.RETARGET_INTERVAL != 0:
            return chain[-1].difficulty

        # Never use the genesis block as a timing reference: its timestamp
        # is a fixed constant (0.0), required for deterministic chain
        # identity, not a real wall-clock time. A window that included it
        # would see an enormous synthetic time delta on the very first
        # retarget (real epoch time minus 0) and crash difficulty to the
        # floor on every real deployment. So the window always starts at
        # block index >= 1.
        window_start_index = max(1, height - self.RETARGET_INTERVAL)
        window_end_index   = height - 1
        num_intervals       = window_end_index - window_start_index

        if num_intervals <= 0:
            # Not enough real-block history yet to measure a window.
            return chain[-1].difficulty

        window_start  = chain[window_start_index]
        window_end    = chain[window_end_index]
        actual_time   = window_end.timestamp - window_start.timestamp
        expected_time = num_intervals * self.TARGET_BLOCK_TIME
        current       = window_end.difficulty

        # Guard against a non-positive window (clock skew, or a test
        # chain mined faster than timestamp resolution allows).
        if actual_time <= 0:
            actual_time = 1e-9

        if actual_time < expected_time / 2:
            new_difficulty = current + 1
        elif actual_time > expected_time * 2:
            new_difficulty = current - 1
        else:
            new_difficulty = current

        return max(self.MIN_DIFFICULTY, min(self.MAX_DIFFICULTY, new_difficulty))

    @staticmethod
    def cumulative_work(chain: list) -> int:
        """
        Total proof-of-work represented by a chain, used to decide which
        of two competing chains is the "real" one during fork resolution.

        A block requiring d leading hex-zero digits takes an expected
        16**d hash attempts to find (each hex digit has a 1-in-16 chance
        of being zero), so summing 16**difficulty across blocks gives a
        chain's total expected mining effort.

        This matters specifically because difficulty now varies over
        time (see expected_difficulty() above): comparing chains by
        raw block count, as before, is no longer equivalent to comparing
        by actual work done. A chain of many easy low-difficulty blocks
        could out-length a chain of fewer but harder blocks -- letting
        an attacker who can influence a chain's retarget history (e.g.
        by fabricating block timestamps) mine a cheap alternate chain
        that out-races a legitimate one under a pure "longest wins" rule.
        Comparing cumulative work instead of length closes that gap.

        The genesis block is excluded: it is identical across every
        valid chain (is_valid() enforces this), so including it would
        only add a constant offset to both sides of any comparison.
        """
        return sum(16 ** block.difficulty for block in chain[1:])

    @staticmethod
    def _median_time_past(chain: list, i: int) -> float:
        """
        Median timestamp of up to MEDIAN_TIME_SPAN blocks immediately
        preceding index i in `chain` (Bitcoin's median-time-past rule).

        Why not just require current.timestamp > previous.timestamp?
        That was the original check here, and it is enough to stop a
        block from being backdated before its own parent -- but it is
        not enough to stop a more patient attacker. Since the parent's
        timestamp is itself attacker-controlled data in a chain they
        are constructing, they could set up a series of blocks that are
        each individually increasing versus their own immediate parent,
        while still steering the sequence toward a favorable difficulty
        retarget outcome. Comparing against the median of a whole
        recent window instead of a single value is far more resistant
        to that: moving the median meaningfully requires manipulating
        most of the window, not just the one block right before the
        new one. It also matches real-world behavior better -- a
        block's timestamp is allowed to sit before its immediate
        parent's (real miners' clocks are never perfectly
        synchronized), as long as it still exceeds the recent median.
        """
        window = chain[max(0, i - Blockchain.MEDIAN_TIME_SPAN):i]
        timestamps = sorted(b.timestamp for b in window)
        mid = len(timestamps) // 2
        if len(timestamps) % 2 == 1:
            return timestamps[mid]
        return (timestamps[mid - 1] + timestamps[mid]) / 2

    # ─────────────────────────────────────────────────────────
    # Mining
    # ─────────────────────────────────────────────────────────

    def _mine(self, block: Block) -> None:
        target = "0" * block.difficulty
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
        if any(tx.is_coinbase for tx in transactions):
            raise ChainError("coinbase transactions cannot be submitted")

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
            previous_hash = self.chain[-1].hash,
            difficulty    = self.expected_difficulty()
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

    def coinbase_reward_is_valid(self, block: Block) -> bool:
        if not block.transactions or not block.transactions[0].is_coinbase:
            return False

        outputs = block.transactions[0].outputs
        if len(outputs) != 1:
            return False
        amount = outputs[0].get("amount")
        if (
            isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or not math.isfinite(amount)
            or amount <= 0
        ):
            return False

        fees = sum(tx.fee for tx in block.transactions[1:])
        expected = round(self.block_reward(block.index) + fees, 8)
        actual = round(block.transactions[0].total_output(), 8)
        return actual == expected

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
        if not self.chain:
            return False

        genesis = self.chain[0]
        if (
            genesis.index != 0
            or genesis.timestamp != 0.0
            or genesis.previous_hash != "0" * 64
            or genesis.transactions
            or genesis.nonce != self.GENESIS_NONCE
            or genesis.difficulty != self.INITIAL_DIFFICULTY
            or genesis.hash != self.GENESIS_HASH
            or not genesis.is_internally_valid()
        ):
            print("invalid genesis block")
            return False

        replay_utxo = UTXOSet()

        for i in range(1, len(self.chain)):
            current  = self.chain[i]
            previous = self.chain[i - 1]

            if current.index != i or previous.index != i - 1:
                print(f"index failure at block {i}")
                return False

            if not current.is_internally_valid():
                print(f"integrity failure at block {i}")
                return False

            if current.previous_hash != previous.hash:
                print(f"linkage failure at block {i}")
                return False

            # Timestamp must exceed the median of the recent window, not
            # merely its immediate parent -- see _median_time_past() for
            # why. This is still safe to check unconditionally on replay:
            # it depends only on the chain's own recorded timestamps, not
            # the current wall clock.
            mtp = self._median_time_past(self.chain, i)
            if current.timestamp <= mtp:
                print(
                    f"timestamp at block {i} does not exceed "
                    f"median-time-past ({current.timestamp} <= {mtp})"
                )
                return False

            # A block dated too far into the future relative to the
            # wall clock at validation time is rejected outright. This
            # is safe to check even on replay of an old, legitimate
            # chain: real time only moves forward, so a block that was
            # NOT future-dated when it was actually created can never
            # start failing this check later -- it only ever catches a
            # block that, right now, still claims a suspiciously future
            # timestamp (fabrication, or severe clock skew on the
            # mining node).
            if current.timestamp > time.time() + self.MAX_FUTURE_DRIFT_SEC:
                print(f"timestamp too far in the future at block {i}")
                return False

            expected_diff = self.expected_difficulty(self.chain[:i])
            if current.difficulty != expected_diff:
                print(
                    f"difficulty failure at block {i}: "
                    f"expected {expected_diff}, got {current.difficulty}"
                )
                return False

            if not current.hash.startswith("0" * current.difficulty):
                print(f"proof-of-work failure at block {i}")
                return False

            # First transaction must be coinbase
            if not current.transactions or not current.transactions[0].is_coinbase:
                print(f"missing coinbase at block {i}")
                return False

            if not self.coinbase_reward_is_valid(current):
                print(f"invalid coinbase reward at block {i}")
                return False

            for j, tx in enumerate(current.transactions):
                if j == 0:
                    if not tx.is_valid():
                        print(f"invalid coinbase at block {i}")
                        return False

                    # Coinbase: apply outputs, skip input validation
                    for k, out in enumerate(tx.outputs):
                        replay_utxo.add(UTXO(tx.tx_id, k, out["address"], out["amount"]))
                    continue

                if tx.is_coinbase:
                    print(f"extra coinbase at block {i} tx {j}")
                    return False

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