import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import api


class FakeTransaction:
    def __init__(self, tx_id, inputs):
        self.tx_id = tx_id
        self.inputs = inputs


class FakeBlock:
    index = 1
    hash = "block-hash"
    previous_hash = "tip-hash"
    nonce = 7

    def __init__(self, transactions):
        self.transactions = transactions

    def transaction_count(self):
        return len(self.transactions)


class FakeChain:
    def __init__(self, block):
        self.chain = [SimpleNamespace(hash="tip-hash")]
        self.block = block
        self.utxo_set = SimpleNamespace(size=lambda: 1)

    def mine_block(self, transactions, miner_address):
        return self.block

    def append_block(self, block):
        self.chain.append(block)

    def height(self):
        return len(self.chain)


class FakeNode:
    port = 8123
    mining = False
    miner_address = "miner-address"

    def __init__(self, block, mempool):
        self.BLOCK_SIZE = 1
        self.chain = FakeChain(block)
        self.mempool = mempool
        self.seen_block_hashes = set()
        self.recompute_calls = 0
        self._broadcast = AsyncMock()

    def _save_chain(self):
        pass

    def _recompute_pending_inputs(self):
        self.recompute_calls += 1


class FakeRequest:
    def __init__(self, node):
        self.app = {"node_map": {node.port: node}}
        self.match_info = {"port": str(node.port)}


class TestForceMine(unittest.IsolatedAsyncioTestCase):
    async def test_force_mine_rebuilds_pending_inputs(self):
        confirmed = FakeTransaction("confirmed", [{"tx_id": "a", "index": 0}])
        remaining = FakeTransaction("remaining", [{"tx_id": "b", "index": 0}])
        coinbase = SimpleNamespace(total_output=lambda: 50.0)
        block = FakeBlock([coinbase, confirmed])
        node = FakeNode(block, [confirmed, remaining])

        with patch.object(api, "serialize_block", return_value={}), patch.object(
            api, "build", return_value="block-message"
        ):
            response = await api.handle_force_mine(FakeRequest(node))

        self.assertEqual(response.status, 200)
        self.assertEqual([tx.tx_id for tx in node.mempool], ["remaining"])
        self.assertEqual(node.recompute_calls, 1)
        node._broadcast.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
