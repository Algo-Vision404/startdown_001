import json
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

    def test_corrupt_utxo_snapshot_is_rejected(self):
        chain = Blockchain()
        chain.add_block([], "miner-address")

        with tempfile.TemporaryDirectory() as data_dir:
            store = ChainStore(23124, data_dir)
            store.save(chain)
            with open(store.utxo_path, "r") as file:
                utxos = json.load(file)
            utxos[0]["amount"] = 999.0
            with open(store.utxo_path, "w") as file:
                json.dump(utxos, file)

            restored = store.load()

        self.assertIsNone(restored)


if __name__ == "__main__":
    unittest.main()
