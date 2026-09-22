import json
import os
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

    def test_stale_utxo_snapshot_is_repaired_from_the_validated_chain(self):
        # save() writes the chain file and the UTXO file as two separate
        # atomic writes, not one atomic unit -- simulate a crash between
        # them by hand-corrupting just the UTXO file afterward. Since the
        # chain itself is still fully valid, load() should repair the
        # stale snapshot rather than discarding the whole chain over it.
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

            self.assertIsNotNone(restored)
            self.assertTrue(restored.is_valid())
            self.assertEqual(restored.height(), chain.height())
            self.assertEqual(restored.utxo_set.size(), chain.utxo_set.size())

            # The on-disk snapshot should have actually been rewritten,
            # not just patched in memory -- a second load() shouldn't
            # need to repair anything again.
            with open(store.utxo_path, "r") as file:
                repaired = json.load(file)
            self.assertNotEqual(repaired[0]["amount"], 999.0)

    def test_missing_utxo_snapshot_is_reconstructed_from_the_chain(self):
        chain = Blockchain()
        chain.add_block([], "miner-address")

        with tempfile.TemporaryDirectory() as data_dir:
            store = ChainStore(23125, data_dir)
            store.save(chain)
            os.remove(store.utxo_path)

            restored = store.load()

        self.assertIsNotNone(restored)
        self.assertTrue(restored.is_valid())
        self.assertEqual(restored.utxo_set.size(), chain.utxo_set.size())

    def test_genuinely_corrupt_chain_is_still_rejected(self):
        # A stale UTXO snapshot is repaired (above), but the chain data
        # itself failing its own validation is a different situation --
        # that's not explainable by an ordinary partial-write race, and
        # there is no valid chain underneath to repair from, so this
        # must still be discarded entirely.
        chain = Blockchain()
        chain.add_block([], "miner-address")

        with tempfile.TemporaryDirectory() as data_dir:
            store = ChainStore(23126, data_dir)
            store.save(chain)
            with open(store.chain_path, "r") as file:
                blocks = json.load(file)
            blocks[1]["nonce"] += 1  # invalidates that block's hash/PoW
            with open(store.chain_path, "w") as file:
                json.dump(blocks, file)

            restored = store.load()

        self.assertIsNone(restored)


if __name__ == "__main__":
    unittest.main()