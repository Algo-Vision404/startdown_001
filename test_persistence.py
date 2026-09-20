import tempfile
import unittest

from chain import Blockchain
from storage import ChainStore


class TestChainPersistence(unittest.TestCase):
    def test_saved_chain_reloads_with_merkle_state(self):
        chain = Blockchain()
        chain.add_block([], "miner-address")

        with tempfile.TemporaryDirectory() as data_dir:
            store = ChainStore(23123, data_dir)
            store.save(chain)
            restored = store.load()

        self.assertIsNotNone(restored)
        self.assertTrue(restored.is_valid())
        self.assertEqual(restored.height(), chain.height())
        self.assertEqual(
            restored.chain[1].merkle_root,
            chain.chain[1].merkle_root,
        )
        self.assertEqual(
            restored.chain[1]._merkle_tree.root,
            restored.chain[1].merkle_root,
        )
        self.assertEqual(restored.utxo_set.size(), chain.utxo_set.size())


if __name__ == "__main__":
    unittest.main()
