import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from message import MessageType, build
from node import Node


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


if __name__ == "__main__":
    unittest.main()
