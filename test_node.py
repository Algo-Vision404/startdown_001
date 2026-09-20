import tempfile
import unittest
from unittest.mock import AsyncMock

from message import MessageType, build
from node import Node


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


if __name__ == "__main__":
    unittest.main()
