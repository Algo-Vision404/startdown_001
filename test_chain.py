import unittest

from Transaction import Transaction
from chain import Blockchain
from utxo import UTXOSet


class TestCoinbaseRules(unittest.TestCase):
    def test_only_first_transaction_in_sequence_may_be_coinbase(self):
        first = Transaction.coinbase("miner-a", 50.0)
        second = Transaction.coinbase("miner-b", 50.0)

        self.assertFalse(
            Blockchain.validate_transaction_sequence(
                [first, second],
                UTXOSet(),
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


if __name__ == "__main__":
    unittest.main()
