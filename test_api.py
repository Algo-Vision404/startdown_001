import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import api
from block import Block


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


class JsonRequest:
    def __init__(self, body):
        self.body = body
        self.app = {}
        self.match_info = {}

    async def json(self):
        return self.body


class MerkleTransaction:
    def __init__(self, tx_id):
        self.tx_id = tx_id
        self.inputs = []
        self.outputs = []
        self.fee = 0.0
        self.is_coinbase = False
        self.timestamp = 0.0

    def to_bytes(self):
        return self.tx_id.encode()


class BlockRequest:
    def __init__(self, block):
        self.app = {"node_map": {8123: SimpleNamespace(
            port=8123,
            chain=SimpleNamespace(chain=[block], height=lambda: 1)
        )}}
        self.match_info = {
            "port": "8123",
            "index": "0",
            "tx_index": "0",
        }


class TestForceMine(unittest.IsolatedAsyncioTestCase):
    async def test_force_mine_rejects_when_node_is_already_mining(self):
        block = FakeBlock([])
        node = FakeNode(block, [])
        node.mining = True

        response = await api.handle_force_mine(FakeRequest(node))

        self.assertEqual(response.status, 409)
        self.assertTrue(node.mining)

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


class TestApiJsonContracts(unittest.IsolatedAsyncioTestCase):
    async def test_wallet_create_rejects_non_object_json(self):
        response = await api.handle_wallet_create(JsonRequest(["alice"]))

        self.assertEqual(response.status, 400)

    async def test_proof_verification_rejects_non_object_json(self):
        response = await api.handle_verify_proof(JsonRequest("not-an-object"))

        self.assertEqual(response.status, 400)


class TestMerkleRoutes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        transactions = [
            MerkleTransaction("a" * 64),
            MerkleTransaction("b" * 64),
        ]
        self.block = Block(0, transactions, "0" * 64, difficulty=4)

    async def test_merkle_root_and_header_routes(self):
        request = BlockRequest(self.block)

        root_response = await api.handle_merkle_root(request)
        header_response = await api.handle_block_header(request)

        root = json.loads(root_response.text)
        header = json.loads(header_response.text)
        self.assertEqual(root_response.status, 200)
        self.assertEqual(root["merkle_root"], self.block.merkle_root)
        self.assertEqual(root["tree"]["root"], self.block.merkle_root)
        self.assertEqual(header_response.status, 200)
        self.assertEqual(header["merkle_root"], self.block.merkle_root)

    async def test_merkle_proof_route_and_verification_route(self):
        request = BlockRequest(self.block)
        proof_response = await api.handle_merkle_proof(request)
        proof_data = json.loads(proof_response.text)

        verify_request = SimpleNamespace(
            json=lambda: asyncio.sleep(0, result={
                "tx_hash": proof_data["tx_hash"],
                "proof": proof_data["proof"],
                "merkle_root": proof_data["merkle_root"],
            })
        )
        verify_response = await api.handle_verify_proof(verify_request)

        self.assertEqual(proof_response.status, 200)
        self.assertEqual(proof_data["tx_index"], 0)
        self.assertEqual(verify_response.status, 200)
        self.assertTrue(json.loads(verify_response.text)["valid"])

    async def test_merkle_proof_route_returns_not_found_for_invalid_index(self):
        request = BlockRequest(self.block)
        request.match_info["tx_index"] = "9"

        response = await api.handle_merkle_proof(request)

        self.assertEqual(response.status, 404)

        request.match_info["tx_index"] = "-1"
        response = await api.handle_merkle_proof(request)

        self.assertEqual(response.status, 404)

    def test_merkle_tree_rejects_negative_indexes(self):
        tree = self.block._merkle_tree

        self.assertIsNone(tree.proof(-1))
        self.assertIsNone(tree.leaf_hash(-1))


if __name__ == "__main__":
    unittest.main()