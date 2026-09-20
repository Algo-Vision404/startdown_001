import unittest

from Transaction import Transaction
from chain import Blockchain
from merkle import MerkleTree
from utxo import UTXO, UTXOSet
from wallet import QuantumWallet


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


if __name__ == "__main__":
    unittest.main()
