import unittest
import math
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


if __name__ == "__main__":
    unittest.main()