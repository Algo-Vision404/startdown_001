import unittest
import math
import time
from types import SimpleNamespace

from Transaction import Transaction
from chain import Blockchain
from merkle import MerkleTree
from utxo import UTXO, UTXOSet
from wallet import QuantumWallet


def _fake_chain(difficulties_and_timestamps):
    """
    Build a lightweight stand-in for a list of Blocks, carrying only the
    two attributes expected_difficulty() touches: difficulty and
    timestamp. Index 0 plays the role of genesis.
    """
    return [
        SimpleNamespace(difficulty=d, timestamp=t)
        for d, t in difficulties_and_timestamps
    ]


class TestCoinbaseRules(unittest.TestCase):
    def test_genesis_block_is_deterministic(self):
        first = Blockchain()
        second = Blockchain()

        self.assertEqual(first.chain[0].hash, second.chain[0].hash)
        self.assertEqual(first.chain[0].timestamp, 0.0)

    def test_chain_rejects_replaced_genesis(self):
        chain = Blockchain()
        chain.chain[0].nonce += 1
        chain.chain[0].recompute_hash()

        self.assertFalse(chain.is_valid())

    def test_chain_rejects_non_sequential_block_index(self):
        chain = Blockchain()
        block = chain.mine_block([], "miner")
        block.index = -1
        block.recompute_hash()
        chain._mine(block)
        chain.append_block(block)

        self.assertFalse(chain.is_valid())

    def test_only_first_transaction_in_sequence_may_be_coinbase(self):
        first = Transaction.coinbase("miner-a", 50.0)
        second = Transaction.coinbase("miner-b", 50.0)

        self.assertFalse(
            Blockchain.validate_transaction_sequence(
                [first, second],
                UTXOSet(),
            )
        )

    def test_block_sequence_rejects_inflated_fee(self):
        wallet = QuantumWallet()
        source_id = "1" * 64
        utxo_set = UTXOSet()
        utxo_set.add(UTXO(source_id, 0, wallet.address, 10.0))
        transaction = Transaction(
            sender_address=wallet.address,
            inputs=[{"tx_id": source_id, "index": 0}],
            outputs=[{"address": "recipient", "amount": 9.0}],
            fee=5.0,
        )
        transaction.sign(wallet)

        self.assertFalse(
            Blockchain.validate_transaction_sequence(
                [transaction],
                utxo_set,
            )
        )

    def test_block_sequence_rejects_non_finite_fee(self):
        wallet = QuantumWallet()
        source_id = "2" * 64
        utxo_set = UTXOSet()
        utxo_set.add(UTXO(source_id, 0, wallet.address, 10.0))
        transaction = Transaction(
            sender_address=wallet.address,
            inputs=[{"tx_id": source_id, "index": 0}],
            outputs=[{"address": "recipient", "amount": 9.0}],
            fee=math.nan,
        )
        transaction.sign(wallet)

        self.assertFalse(
            Blockchain.validate_transaction_sequence(
                [transaction],
                utxo_set,
            )
        )

    def test_mine_block_rejects_submitted_coinbase(self):
        chain = Blockchain()
        submitted = Transaction.coinbase("attacker", 1000.0)

        with self.assertRaisesRegex(Exception, "coinbase"):
            chain.mine_block([submitted], "miner")

    def test_coinbase_reward_must_match_protocol_reward(self):
        chain = Blockchain()
        block = chain.mine_block([], "miner")
        block.transactions[0].outputs[0]["amount"] = 1000.0

        self.assertFalse(chain.coinbase_reward_is_valid(block))

    def test_coinbase_must_have_one_positive_output(self):
        chain = Blockchain()
        block = chain.mine_block([], "miner")
        block.transactions[0].outputs.append({
            "address": "attacker",
            "amount": -49.0,
        })

        self.assertFalse(chain.coinbase_reward_is_valid(block))

    def test_full_chain_validation_rejects_extra_coinbase(self):
        chain = Blockchain()
        block = chain.mine_block([], "miner")
        block.transactions.append(Transaction.coinbase("attacker", 1.0))
        block._merkle_tree = MerkleTree(block.transactions)
        block.merkle_root = block._merkle_tree.root
        block.nonce = 0
        block.recompute_hash()
        chain._mine(block)
        chain.append_block(block)

        self.assertFalse(chain.is_valid())

    def test_full_chain_validation_rejects_tampered_coinbase_id(self):
        chain = Blockchain()
        block = chain.mine_block([], "miner")
        block.transactions[0].tx_id = "f" * 64
        block._merkle_tree = MerkleTree(block.transactions)
        block.merkle_root = block._merkle_tree.root
        block.nonce = 0
        block.recompute_hash()
        chain._mine(block)
        chain.append_block(block)

        self.assertFalse(chain.is_valid())


class TestDifficultyRetargeting(unittest.TestCase):
    def test_difficulty_holds_steady_below_first_retarget_window(self):
        chain = Blockchain()
        history = [(4, 0.0)] + [(4, float(i)) for i in range(1, 6)]
        self.assertEqual(chain.expected_difficulty(_fake_chain(history)), 4)

    def test_difficulty_increases_when_blocks_mined_too_fast(self):
        chain = Blockchain()
        # genesis + blocks 1..9 -> height 10 triggers the first retarget.
        # Window is blocks[1..9]: 8 intervals * 30s target = 240s expected.
        # Mined within ~1 second total, well under half the expected time.
        history = [(4, 0.0)] + [(4, i * 0.1) for i in range(1, 10)]
        self.assertEqual(chain.expected_difficulty(_fake_chain(history)), 5)

    def test_difficulty_decreases_when_blocks_mined_too_slow(self):
        chain = Blockchain()
        # Same shape, spread far past 2x the 240s expected window.
        history = [(4, 0.0)] + [(4, i * 100.0) for i in range(1, 10)]
        self.assertEqual(chain.expected_difficulty(_fake_chain(history)), 3)

    def test_difficulty_unchanged_within_tolerance(self):
        chain = Blockchain()
        # 8 intervals at exactly the 30s target -> right at the expected
        # time, comfortably inside the [0.5x, 2x] tolerance band.
        history = [(4, 0.0)] + [(4, 1.0 + i * 30.0) for i in range(0, 9)]
        self.assertEqual(chain.expected_difficulty(_fake_chain(history)), 4)

    def test_difficulty_does_not_drop_below_minimum(self):
        chain = Blockchain()
        history = [(chain.MIN_DIFFICULTY, 0.0)] + [
            (chain.MIN_DIFFICULTY, i * 1000.0) for i in range(1, 10)
        ]
        self.assertEqual(
            chain.expected_difficulty(_fake_chain(history)),
            chain.MIN_DIFFICULTY
        )

    def test_difficulty_does_not_rise_above_maximum(self):
        chain = Blockchain()
        history = [(chain.MAX_DIFFICULTY, 0.0)] + [
            (chain.MAX_DIFFICULTY, i * 0.001) for i in range(1, 10)
        ]
        self.assertEqual(
            chain.expected_difficulty(_fake_chain(history)),
            chain.MAX_DIFFICULTY
        )

    def test_genesis_timestamp_is_never_used_as_timing_reference(self):
        # Genesis is pinned at timestamp 0.0 for deterministic chain
        # identity. If a retarget window used it as a reference point,
        # "actual_time" would be a real Unix epoch value (~1.7 billion
        # seconds) against a 240s expectation, and difficulty would
        # collapse to the floor on every real deployment. It should not.
        chain = Blockchain()
        history = [(4, 0.0)] + [
            (4, 1_700_000_000.0 + i * 30.0) for i in range(1, 10)
        ]
        self.assertEqual(chain.expected_difficulty(_fake_chain(history)), 4)

    def test_full_chain_validation_rejects_mismatched_difficulty(self):
        chain = Blockchain()
        block = chain.mine_block([], "miner")

        # Force a difficulty inconsistent with what expected_difficulty()
        # requires for this position, keeping the header hash internally
        # consistent by re-mining under the (wrong, easier) target.
        block.difficulty = chain.MIN_DIFFICULTY
        block.nonce = 0
        block.recompute_hash()
        chain._mine(block)
        chain.append_block(block)

        self.assertFalse(chain.is_valid())

    def test_full_chain_validation_rejects_non_increasing_timestamp(self):
        chain = Blockchain()
        block = chain.mine_block([], "miner")

        # Backdate the block to the same instant as genesis (0.0).
        # Header hash must be recomputed to stay internally consistent,
        # and PoW re-mined so it still satisfies its own difficulty --
        # the only thing this should fail on is the timestamp check.
        block.timestamp = 0.0
        block.recompute_hash()
        chain._mine(block)
        chain.append_block(block)

        self.assertFalse(chain.is_valid())

    def test_full_chain_validation_rejects_far_future_timestamp(self):
        chain = Blockchain()
        block = chain.mine_block([], "miner")

        # Well past MAX_FUTURE_DRIFT_SEC (2 hours) ahead of "now".
        # Same pattern as the backdating test above: recompute the
        # header hash and re-mine PoW so only the drift check can fail.
        block.timestamp = time.time() + chain.MAX_FUTURE_DRIFT_SEC + 3600
        block.recompute_hash()
        chain._mine(block)
        chain.append_block(block)

        self.assertFalse(chain.is_valid())

    def test_median_time_past_allows_timestamp_before_immediate_parent(self):
        # This is the exact behavior a naive "must exceed immediate
        # parent" check gets wrong: real miners' clocks aren't
        # perfectly synchronized, and Bitcoin-style chains allow a
        # block's timestamp to sit before its parent's, as long as it
        # still exceeds the median of the recent window. Mine two real
        # blocks (so there's a real timing gap between them), then set
        # a third block's timestamp to fall strictly between them --
        # below its immediate parent (b2), but above the window's
        # median (which, for i=3, is b1's timestamp).
        chain = Blockchain()
        b1 = chain.mine_block([], "miner")
        chain.append_block(b1)
        b2 = chain.mine_block([], "miner")
        chain.append_block(b2)

        self.assertLess(b1.timestamp, b2.timestamp)

        b3 = chain.mine_block([], "miner")
        b3.timestamp = (b1.timestamp + b2.timestamp) / 2
        self.assertLess(b3.timestamp, b2.timestamp)  # below immediate parent
        b3.recompute_hash()
        chain._mine(b3)
        chain.append_block(b3)

        self.assertTrue(chain.is_valid())

    def test_median_time_past_still_rejects_timestamp_below_median(self):
        # Complement to the test above: a timestamp that fails to clear
        # even the window's median is still rejected, regardless of how
        # it compares to the immediate parent.
        chain = Blockchain()
        b1 = chain.mine_block([], "miner")
        chain.append_block(b1)
        b2 = chain.mine_block([], "miner")
        chain.append_block(b2)

        b3 = chain.mine_block([], "miner")
        b3.timestamp = b1.timestamp - 1.0  # below the median (b1's timestamp)
        b3.recompute_hash()
        chain._mine(b3)
        chain.append_block(b3)

        self.assertFalse(chain.is_valid())


class TestCumulativeWork(unittest.TestCase):
    def test_excludes_genesis(self):
        chain = [
            SimpleNamespace(difficulty=4, timestamp=0.0),  # genesis
        ]
        self.assertEqual(Blockchain.cumulative_work(chain), 0)

    def test_sums_16_pow_difficulty_across_real_blocks(self):
        chain = _fake_chain([(4, 0.0), (4, 1.0), (5, 2.0)])
        expected = 16 ** 4 + 16 ** 5
        self.assertEqual(Blockchain.cumulative_work(chain), expected)

    def test_shorter_harder_chain_outweighs_longer_easier_chain(self):
        # This is the exact scenario a pure "longest chain wins" rule
        # gets wrong once difficulty varies: 3 blocks at difficulty 8
        # represent far more real work than 50 blocks at difficulty 1,
        # even though the second chain has many more blocks.
        heavy_short = _fake_chain([(8, 0.0)] + [(8, float(i)) for i in range(1, 4)])
        light_long  = _fake_chain([(1, 0.0)] + [(1, float(i)) for i in range(1, 51)])

        self.assertGreater(
            Blockchain.cumulative_work(heavy_short),
            Blockchain.cumulative_work(light_long)
        )
        self.assertGreater(len(light_long), len(heavy_short))


class TestMedianTimePast(unittest.TestCase):
    def test_odd_window_returns_middle_value(self):
        chain = _fake_chain([(4, 0.0), (4, 10.0), (4, 20.0)])
        # window for i=3 is the whole chain: [0.0, 10.0, 20.0] -> median 10.0
        self.assertEqual(Blockchain._median_time_past(chain, 3), 10.0)

    def test_even_window_returns_average_of_middle_two(self):
        chain = _fake_chain([(4, 0.0), (4, 10.0), (4, 20.0), (4, 30.0)])
        # window for i=4: [0.0, 10.0, 20.0, 30.0] -> avg(10.0, 20.0) = 15.0
        self.assertEqual(Blockchain._median_time_past(chain, 4), 15.0)

    def test_window_caps_at_median_time_span(self):
        # 15 blocks; only the most recent MEDIAN_TIME_SPAN (11) should
        # count toward the median for the 15th position.
        chain = _fake_chain([(4, float(i)) for i in range(15)])
        window_start = 15 - Blockchain.MEDIAN_TIME_SPAN  # = 4
        expected = sorted(float(i) for i in range(window_start, 15))
        mid = len(expected) // 2
        expected_median = expected[mid]  # odd count (11) -> exact middle
        self.assertEqual(
            Blockchain._median_time_past(chain, 15),
            expected_median
        )

    def test_unsorted_input_is_sorted_before_taking_median(self):
        chain = _fake_chain([(4, 30.0), (4, 10.0), (4, 20.0)])
        self.assertEqual(Blockchain._median_time_past(chain, 3), 20.0)


class TestTransactionsFor(unittest.TestCase):
    def setUp(self):
        self.chain = Blockchain()
        self.alice = QuantumWallet()
        self.bob   = QuantumWallet()

        # Block 1: coinbase reward to alice.
        self.chain.add_block([], self.alice.address)

        # Block 2: alice sends bob 10, keeping the rest as change. Mined
        # by a different address so alice's only involvement here is
        # the transfer itself, not a second coinbase reward.
        self.transfer = Transaction.transfer(
            sender_wallet=self.alice,
            utxo_set=self.chain.utxo_set,
            recipient_address=self.bob.address,
            amount=10.0,
            fee=1.0,
        )
        self.chain.add_block([self.transfer], "other-miner")

    def test_unrelated_address_has_no_history(self):
        self.assertEqual(self.chain.transactions_for("nobody"), [])

    def test_coinbase_recipient_shows_as_received_not_sent(self):
        entries = self.chain.transactions_for(self.alice.address)
        coinbase_entry = next(
            e for e in entries if e["transaction"].is_coinbase
        )

        self.assertFalse(coinbase_entry["is_sender"])
        self.assertEqual(coinbase_entry["received_amount"], 50.0)

    def test_sender_sees_their_own_change_as_received(self):
        entries = self.chain.transactions_for(self.alice.address)
        transfer_entry = next(
            e for e in entries if not e["transaction"].is_coinbase
        )

        self.assertTrue(transfer_entry["is_sender"])
        # 50 (coinbase) - 10 (to bob) - 1 (fee) = 39 change back to alice.
        self.assertEqual(transfer_entry["received_amount"], 39.0)

    def test_recipient_sees_amount_received_but_is_not_sender(self):
        entries = self.chain.transactions_for(self.bob.address)

        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0]["is_sender"])
        self.assertEqual(entries[0]["received_amount"], 10.0)

    def test_results_are_most_recent_block_first(self):
        entries = self.chain.transactions_for(self.alice.address)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["block_index"], 2)  # the transfer
        self.assertEqual(entries[1]["block_index"], 1)  # the coinbase

    def test_limit_caps_results_to_the_most_recent(self):
        entries = self.chain.transactions_for(self.alice.address, limit=1)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["block_index"], 2)


if __name__ == "__main__":
    unittest.main()