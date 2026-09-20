import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from message import MessageType, build
from node import Node
from Transaction import Transaction
from utxo import UTXO
from wallet import QuantumWallet


def _fake_tx(tx_id: str, fee: float):
    """
    Minimal stand-in for a Transaction, carrying only the attributes
    the mempool eviction path touches (fee, tx_id, is_coinbase, inputs).
    Avoids needing real signed transactions for pure mempool-list tests.
    """
    return SimpleNamespace(
        tx_id       = tx_id,
        fee         = fee,
        is_coinbase = False,
        inputs      = [{"tx_id": tx_id, "index": 0}],
    )


class TestPeerMessageHandling(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.data_dir = tempfile.TemporaryDirectory()
        self.node = Node("127.0.0.1", 23231, data_dir=self.data_dir.name)
        self.websocket = AsyncMock()

    async def asyncTearDown(self):
        self.data_dir.cleanup()

    async def test_malformed_transaction_does_not_escape_dispatch(self):
        message = build(MessageType.TRANSACTION, 23232, {"missing": "fields"})

        await self.node._process(message, self.websocket)

        self.assertEqual(self.node.mempool, [])

    async def test_malformed_block_does_not_escape_dispatch(self):
        message = build(MessageType.BLOCK, 23232, {"missing": "fields"})

        await self.node._process(message, self.websocket)

        self.assertEqual(self.node.chain.height(), 1)

    async def test_malformed_chain_does_not_escape_sync(self):
        await self.node._on_chain([{"missing": "fields"}, {"also": "bad"}])

        self.assertEqual(self.node.chain.height(), 1)


class TestMempoolEviction(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.data_dir = tempfile.TemporaryDirectory()
        self.node = Node("127.0.0.1", 23233, data_dir=self.data_dir.name)
        # Shrink the cap so the test doesn't need hundreds of entries.
        self.node.MEMPOOL_MAX_SIZE = 3

    async def asyncTearDown(self):
        self.data_dir.cleanup()

    async def test_eviction_keeps_only_highest_fee_transactions(self):
        # Fees intentionally out of order; the mempool is expected to be
        # sorted (highest fee first) before eviction runs, as _on_transaction
        # does. Simulate that ordering directly here.
        txs = [_fake_tx(f"tx{i}", fee) for i, fee in enumerate([5, 1, 3, 4, 2])]
        self.node.mempool = sorted(txs, key=lambda t: t.fee, reverse=True)
        for tx in txs:
            self.node.seen_tx_ids.add(tx.tx_id)
        self.node._recompute_pending_inputs()

        self.node._evict_excess_mempool()

        self.assertEqual(len(self.node.mempool), 3)
        self.assertEqual(
            {tx.fee for tx in self.node.mempool},
            {5, 4, 3}
        )

    async def test_evicted_transactions_are_forgotten(self):
        txs = [_fake_tx(f"tx{i}", fee) for i, fee in enumerate([5, 1, 3, 4, 2])]
        self.node.mempool = sorted(txs, key=lambda t: t.fee, reverse=True)
        for tx in txs:
            self.node.seen_tx_ids.add(tx.tx_id)
        self.node._recompute_pending_inputs()

        self.node._evict_excess_mempool()

        remaining_ids = {tx.tx_id for tx in self.node.mempool}
        evicted = [tx for tx in txs if tx.tx_id not in remaining_ids]
        self.assertEqual(len(evicted), 2)

        for tx in evicted:
            self.assertNotIn(tx.tx_id, self.node.seen_tx_ids)
            self.assertNotIn(
                (tx.tx_id, 0), self.node.pending_inputs
            )

    async def test_no_eviction_below_cap(self):
        txs = [_fake_tx(f"tx{i}", fee) for i, fee in enumerate([5, 1])]
        self.node.mempool = list(txs)
        for tx in txs:
            self.node.seen_tx_ids.add(tx.tx_id)

        self.node._evict_excess_mempool()

        self.assertEqual(len(self.node.mempool), 2)


class TestMempoolEvictionIntegration(unittest.IsolatedAsyncioTestCase):
    """
    Exercises mempool eviction through the real _on_transaction path --
    signature verification, UTXO validation, and pending-input tracking
    included -- rather than calling _evict_excess_mempool() directly.
    """

    async def asyncSetUp(self):
        self.data_dir = tempfile.TemporaryDirectory()
        self.node = Node("127.0.0.1", 23234, data_dir=self.data_dir.name)
        self.node.MEMPOOL_MAX_SIZE = 3
        # Keep well above the number of transactions submitted below so
        # the auto-mining trigger in _on_transaction never fires and
        # races with the eviction assertions.
        self.node.BLOCK_SIZE = 100

        self.wallet = QuantumWallet()

        # Seed the UTXO set with one spendable output per transaction
        # we're about to submit, each owned by the same wallet.
        self.fees = [5.0, 1.0, 3.0, 4.0, 2.0]
        for i, fee in enumerate(self.fees):
            source_id = str(i + 1) * 64
            self.node.chain.utxo_set.add(
                UTXO(source_id, 0, self.wallet.address, 20.0)
            )

    async def asyncTearDown(self):
        self.data_dir.cleanup()

    def _signed_tx(self, index: int, fee: float) -> Transaction:
        source_id = str(index + 1) * 64
        tx = Transaction(
            sender_address=self.wallet.address,
            inputs=[{"tx_id": source_id, "index": 0}],
            outputs=[{"address": "recipient", "amount": 20.0 - fee}],
            fee=fee,
        )
        tx.sign(self.wallet)
        return tx

    async def test_lowest_fee_real_transactions_are_evicted(self):
        for i, fee in enumerate(self.fees):
            await self.node._on_transaction(self._signed_tx(i, fee))

        self.assertEqual(len(self.node.mempool), 3)
        self.assertEqual(
            {tx.fee for tx in self.node.mempool},
            {5.0, 4.0, 3.0}
        )

    async def test_evicted_transaction_input_can_be_resubmitted_with_higher_fee(self):
        txs = [self._signed_tx(i, fee) for i, fee in enumerate(self.fees)]
        for tx in txs:
            await self.node._on_transaction(tx)

        surviving_ids = {tx.tx_id for tx in self.node.mempool}
        evicted = [tx for tx in txs if tx.tx_id not in surviving_ids]
        self.assertEqual(len(evicted), 2)

        lowest_fee_evicted = min(evicted, key=lambda tx: tx.fee)
        evicted_index = self.fees.index(lowest_fee_evicted.fee)

        # Eviction must have cleared the transaction from seen_tx_ids
        # and its input from pending_inputs, or neither assertion below
        # would be possible.
        self.assertNotIn(lowest_fee_evicted.tx_id, self.node.seen_tx_ids)
        self.assertNotIn(
            (lowest_fee_evicted.inputs[0]["tx_id"], 0),
            self.node.pending_inputs
        )

        # Resubmitting the exact same transaction (same fee) would just
        # get evicted again immediately -- it's still the floor. A real
        # "fee bump" replacement spending the same input, with a fee
        # that beats the current mempool floor, should be accepted and
        # survive.
        current_floor = min(tx.fee for tx in self.node.mempool)
        bumped = self._signed_tx(evicted_index, current_floor + 1.0)

        await self.node._on_transaction(bumped)

        self.assertIn(bumped.tx_id, {tx.tx_id for tx in self.node.mempool})
        self.assertEqual(len(self.node.mempool), 3)

    async def test_pending_inputs_reflect_only_surviving_transactions(self):
        for i, fee in enumerate(self.fees):
            await self.node._on_transaction(self._signed_tx(i, fee))

        surviving_keys = {
            (tx.inputs[0]["tx_id"], tx.inputs[0]["index"])
            for tx in self.node.mempool
        }
        self.assertEqual(self.node.pending_inputs, surviving_keys)

        # The evicted transactions' inputs must not still be marked
        # pending, or a resubmission spending the same input would be
        # wrongly rejected as a double-spend.
        evicted_key = (str(2) * 64, 0)  # index 1 -> source_id "2"*64
        self.assertNotIn(evicted_key, self.node.pending_inputs)


if __name__ == "__main__":
    unittest.main()