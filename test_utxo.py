import unittest

from Transaction import Transaction
from chain import Blockchain
from utxo import UTXO, UTXOSet
from wallet import QuantumWallet


class TestUTXOSetBasics(unittest.TestCase):
    def setUp(self):
        self.utxo_set = UTXOSet()
        self.utxo = UTXO("a" * 64, 0, "alice", 10.0)
        self.utxo_set.add(self.utxo)

    def test_get_returns_the_added_utxo(self):
        self.assertEqual(self.utxo_set.get("a" * 64, 0), self.utxo)

    def test_get_returns_none_for_an_unknown_key(self):
        self.assertIsNone(self.utxo_set.get("b" * 64, 0))

    def test_exists_reflects_presence(self):
        self.assertTrue(self.utxo_set.exists("a" * 64, 0))
        self.assertFalse(self.utxo_set.exists("b" * 64, 0))

    def test_spend_removes_and_returns_the_utxo(self):
        spent = self.utxo_set.spend("a" * 64, 0)

        self.assertEqual(spent, self.utxo)
        self.assertFalse(self.utxo_set.exists("a" * 64, 0))

    def test_spend_is_idempotent_on_an_already_spent_output(self):
        self.utxo_set.spend("a" * 64, 0)

        self.assertIsNone(self.utxo_set.spend("a" * 64, 0))

    def test_size_reflects_additions_and_removals(self):
        self.assertEqual(self.utxo_set.size(), 1)
        self.utxo_set.spend("a" * 64, 0)
        self.assertEqual(self.utxo_set.size(), 0)


class TestBalanceAndLookup(unittest.TestCase):
    def setUp(self):
        self.utxo_set = UTXOSet()
        self.utxo_set.add(UTXO("a" * 64, 0, "alice", 10.0))
        self.utxo_set.add(UTXO("b" * 64, 0, "alice", 5.5))
        self.utxo_set.add(UTXO("c" * 64, 0, "bob", 3.0))

    def test_balance_sums_only_the_given_address(self):
        self.assertEqual(self.utxo_set.balance("alice"), 15.5)
        self.assertEqual(self.utxo_set.balance("bob"), 3.0)

    def test_balance_is_zero_for_an_address_with_no_utxos(self):
        self.assertEqual(self.utxo_set.balance("carol"), 0.0)

    def test_utxos_for_returns_only_the_given_address(self):
        alice_utxos = self.utxo_set.utxos_for("alice")

        self.assertEqual(len(alice_utxos), 2)
        self.assertTrue(all(u.address == "alice" for u in alice_utxos))


class TestSerializationRoundTrip(unittest.TestCase):
    def test_to_dict_and_from_dict_round_trip(self):
        original = UTXOSet()
        original.add(UTXO("a" * 64, 0, "alice", 10.0))
        original.add(UTXO("a" * 64, 1, "bob", 2.5))

        restored = UTXOSet.from_dict(original.to_dict())

        self.assertEqual(restored.size(), original.size())
        self.assertEqual(restored.balance("alice"), original.balance("alice"))
        self.assertEqual(restored.balance("bob"), original.balance("bob"))
        self.assertEqual(
            restored.get("a" * 64, 0).to_dict(),
            original.get("a" * 64, 0).to_dict(),
        )


class TestApplyBlock(unittest.TestCase):
    def test_spending_an_output_created_earlier_in_the_same_block_works(self):
        # apply_block() must remove every transaction's inputs before
        # adding any transaction's outputs -- otherwise a transaction
        # spending an output created earlier in the same block (e.g.
        # immediately spending a coinbase reward) would see that output
        # as already present and unaffected, which happens to still work
        # by accident, but the real risk this guards against is input
        # removal racing against a same-block output of the same key
        # being added after it was meant to be removed. Exercise it with
        # a real chain-mined block, where this ordering is load-bearing.
        chain = Blockchain()
        alice = QuantumWallet()
        bob = QuantumWallet()

        block1 = chain.mine_block([], alice.address)
        chain.append_block(block1)

        tx = Transaction.transfer(
            sender_wallet=alice,
            utxo_set=chain.utxo_set,
            recipient_address=bob.address,
            amount=10.0,
            fee=1.0,
        )
        block2 = chain.mine_block([tx], "other-miner")
        chain.append_block(block2)

        self.assertFalse(
            chain.utxo_set.exists(block1.transactions[0].tx_id, 0)
        )
        self.assertEqual(chain.utxo_set.balance(bob.address), 10.0)


class TestRollbackBlock(unittest.TestCase):
    def test_rollback_restores_the_pre_apply_utxo_set_exactly(self):
        # rollback_block() isn't currently called anywhere in node.py --
        # fork resolution replays a whole candidate chain from a fresh
        # UTXOSet() instead of rolling back incrementally. It's kept as
        # a correct building block for that approach, so this test
        # exists to make sure "correct" is actually true.
        chain = Blockchain()
        alice = QuantumWallet()
        bob = QuantumWallet()

        block1 = chain.mine_block([], alice.address)
        chain.append_block(block1)

        tx = Transaction.transfer(
            sender_wallet=alice,
            utxo_set=chain.utxo_set,
            recipient_address=bob.address,
            amount=10.0,
            fee=1.0,
        )
        block2 = chain.mine_block([tx], "other-miner")

        before = {k: v.to_dict() for k, v in chain.utxo_set._utxos.items()}

        # The caller must capture each input's referenced UTXO before
        # apply_block() consumes it -- apply_block() has no undo log.
        previous_utxos = [
            chain.utxo_set.get(inp["tx_id"], inp["index"]).to_dict()
            for t in block2.transactions
            for inp in t.inputs
        ]

        chain.utxo_set.apply_block(block2)
        self.assertNotEqual(
            {k: v.to_dict() for k, v in chain.utxo_set._utxos.items()},
            before,
        )

        chain.utxo_set.rollback_block(block2, previous_utxos)

        after = {k: v.to_dict() for k, v in chain.utxo_set._utxos.items()}
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
