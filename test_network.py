"""
Integration tests that run real Node instances over real localhost
websocket connections.

Every other test file exercises a single node in isolation, calling its
handlers directly. These tests cover what those cannot: that handshakes,
block gossip, transaction gossip, chain sync, fork resolution and peer
banning actually work between separate nodes talking over sockets.
"""

import asyncio
import itertools
import tempfile
import unittest
from unittest.mock import AsyncMock

from message import MessageType, build, serialize_block
from node import Node
from peer_manager import PeerState
from Transaction import Transaction
from wallet import QuantumWallet

HOST = "127.0.0.1"

# Each node gets its own port so tests never fight over a socket that a
# previous test is still tearing down.
_ports = itertools.count(24200)


async def wait_for(condition, timeout: float = 10.0) -> bool:
    """Poll `condition` until it is true or the timeout expires."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if condition():
            return True
        await asyncio.sleep(0.05)
    return condition()


async def mine_silently(node: Node, miner_address: str):
    """
    Mine one block on `node` without telling anyone (builds a private
    chain). Mining runs in an executor, as it does inside a real node, so
    the event loop keeps serving websocket traffic while it works.
    """
    loop = asyncio.get_running_loop()
    block = await loop.run_in_executor(
        None, node.chain.mine_block, [], miner_address
    )
    node.chain.append_block(block)
    node.seen_block_hashes.add(block.hash)
    return block


async def mine_and_broadcast(node: Node, miner_address: str):
    """Mine one empty block on `node`, append it, and gossip it to peers."""
    block = await mine_silently(node, miner_address)
    await node._broadcast(
        build(MessageType.BLOCK, node.port, serialize_block(block))
    )
    return block


class NetworkTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dirs = []
        self.nodes = []

    async def asyncTearDown(self):
        for node in self.nodes:
            for ws in list(node.peers.values()):
                try:
                    await ws.close()
                except Exception:
                    pass
            if node._server is not None:
                node._server.close()
                await node._server.wait_closed()
        for directory in self.dirs:
            directory.cleanup()

    async def start_node(self) -> Node:
        directory = tempfile.TemporaryDirectory()
        self.dirs.append(directory)
        node = Node(HOST, next(_ports), data_dir=directory.name)
        await node.serve()
        self.nodes.append(node)
        return node

    async def connect(self, dialer: Node, target: Node) -> None:
        connected = await dialer._connect_to(HOST, target.port)
        self.assertTrue(connected, "outbound connection failed")
        # The handshake is processed asynchronously on the target side.
        self.assertTrue(
            await wait_for(lambda: dialer.port in target.peers),
            "target never registered the dialer as a peer",
        )


class TestPeerConnection(NetworkTestCase):
    async def test_handshake_registers_both_sides(self):
        a = await self.start_node()
        b = await self.start_node()

        await self.connect(b, a)

        self.assertIn(a.port, b.peers)
        self.assertIn(b.port, a.peers)


class TestBlockPropagation(NetworkTestCase):
    async def test_mined_block_reaches_the_peer(self):
        a = await self.start_node()
        b = await self.start_node()
        await self.connect(b, a)

        block = await mine_and_broadcast(a, "miner-a")

        self.assertTrue(await wait_for(lambda: b.chain.height() == 2))
        self.assertEqual(b.chain.chain[-1].hash, block.hash)
        self.assertTrue(b.chain.is_valid())

    async def test_block_gossips_across_a_chain_of_nodes(self):
        # a <- b <- c: a block mined on a must reach c through b.
        a = await self.start_node()
        b = await self.start_node()
        c = await self.start_node()
        await self.connect(b, a)
        await self.connect(c, b)

        block = await mine_and_broadcast(a, "miner-a")

        self.assertTrue(await wait_for(lambda: c.chain.height() == 2))
        self.assertEqual(c.chain.chain[-1].hash, block.hash)


class TestInitialSync(NetworkTestCase):
    async def test_new_peer_downloads_the_existing_chain(self):
        a = await self.start_node()
        for _ in range(3):
            await mine_silently(a, "miner-a")
        self.assertEqual(a.chain.height(), 4)

        b = await self.start_node()
        self.assertEqual(b.chain.height(), 1)

        # Connecting sends REQUEST_CHAIN, so b should pull a's chain.
        await self.connect(b, a)

        self.assertTrue(await wait_for(lambda: b.chain.height() == 4))
        self.assertEqual(b.chain.chain[-1].hash, a.chain.chain[-1].hash)
        self.assertTrue(b.chain.is_valid())


class TestTransactionGossip(NetworkTestCase):
    async def test_submitted_transaction_reaches_the_peer_mempool(self):
        a = await self.start_node()
        b = await self.start_node()
        await self.connect(b, a)

        # Fund alice on both chains by gossiping a block that pays her.
        alice = QuantumWallet()
        bob = QuantumWallet()
        await mine_and_broadcast(a, alice.address)
        self.assertTrue(await wait_for(lambda: b.chain.height() == 2))

        tx = Transaction.transfer(
            sender_wallet=alice,
            utxo_set=a.chain.utxo_set,
            recipient_address=bob.address,
            amount=10.0,
            fee=1.0,
        )
        await a.submit_transaction(tx)

        self.assertTrue(await wait_for(lambda: len(b.mempool) == 1))
        self.assertEqual(b.mempool[0].tx_id, tx.tx_id)


class TestForkResolution(NetworkTestCase):
    async def test_peer_with_less_work_adopts_the_heavier_chain(self):
        # Two nodes build different private chains from the same genesis.
        a = await self.start_node()
        b = await self.start_node()
        await mine_silently(a, "miner-a")
        await mine_silently(a, "miner-a")
        await mine_silently(b, "miner-b")
        self.assertNotEqual(a.chain.chain[1].hash, b.chain.chain[1].hash)

        # b dials a and requests its chain. a has more cumulative work.
        await self.connect(b, a)

        self.assertTrue(await wait_for(lambda: b.chain.height() == 3))
        self.assertEqual(b.chain.chain[-1].hash, a.chain.chain[-1].hash)
        self.assertTrue(b.chain.is_valid())

    async def test_node_keeps_its_own_chain_when_the_peer_has_less_work(self):
        a = await self.start_node()
        b = await self.start_node()
        await mine_silently(a, "miner-a")
        await mine_silently(a, "miner-a")
        await mine_silently(b, "miner-b")
        tip_before = a.chain.chain[-1].hash

        # Spy on a's chain handler so the test waits for the peer's reply
        # to actually arrive, rather than sleeping and hoping.
        spy = AsyncMock(wraps=a._on_chain)
        a._on_chain = spy

        # This time the heavier node dials out and asks the lighter one
        # for its chain. It must look at it and refuse to switch.
        await self.connect(a, b)
        self.assertTrue(
            await wait_for(lambda: spy.await_count >= 1),
            "the peer's chain never arrived",
        )

        self.assertEqual(a.chain.height(), 3)
        self.assertEqual(a.chain.chain[-1].hash, tip_before)


class TestPeerBanOverTheWire(NetworkTestCase):
    async def test_peer_sending_malformed_blocks_is_banned_and_dropped(self):
        a = await self.start_node()
        b = await self.start_node()
        await self.connect(a, b)  # a dials b; b now knows a as a peer
        self.assertTrue(await wait_for(lambda: a.port in b.peers))

        garbage = build(MessageType.BLOCK, a.port, {"missing": "fields"})
        for _ in range(b.peer_mgr.BAN_AFTER):
            await a.peers[b.port].send(garbage)

        def a_is_banned():
            peer = next(
                (p for p in b.peer_mgr.all_peers() if p.port == a.port), None
            )
            return peer is not None and peer.state == PeerState.BANNED

        self.assertTrue(await wait_for(a_is_banned))
        self.assertTrue(await wait_for(lambda: a.port not in b.peers))

    async def test_banned_peer_cannot_un_ban_itself_by_reconnecting(self):
        # mark_connected() previously overwrote BANNED with CONNECTED
        # unconditionally, and _on_handshake() never checked ban status
        # before registering a new connection -- so a banned peer could
        # simply reconnect with a fresh websocket and walk right back in,
        # fully un-banned, fail_count reset to 0. Both are fixed now:
        # mark_connected() refuses to resurrect a BANNED peer, and
        # _on_handshake() rejects and closes the connection outright.
        a = await self.start_node()
        b = await self.start_node()
        await self.connect(a, b)
        self.assertTrue(await wait_for(lambda: a.port in b.peers))

        garbage = build(MessageType.BLOCK, a.port, {"missing": "fields"})
        for _ in range(b.peer_mgr.BAN_AFTER):
            await a.peers[b.port].send(garbage)

        def a_banned_on_b():
            peer = next(
                (p for p in b.peer_mgr.all_peers() if p.port == a.port), None
            )
            return peer is not None and peer.state == PeerState.BANNED

        self.assertTrue(await wait_for(a_banned_on_b))
        self.assertTrue(await wait_for(lambda: a.port not in b.peers))

        # a reconnects with a brand new websocket connection.
        reconnected = await a._connect_to(HOST, b.port)
        self.assertTrue(reconnected, "the TCP-level reconnect itself succeeds")

        # b must refuse to ever register a as connected again.
        still_rejected = await wait_for(lambda: a.port in b.peers, timeout=2.0)
        self.assertFalse(still_rejected)

        peer = next(p for p in b.peer_mgr.all_peers() if p.port == a.port)
        self.assertEqual(peer.state, PeerState.BANNED)
        self.assertEqual(peer.fail_count, b.peer_mgr.BAN_AFTER)


if __name__ == "__main__":
    unittest.main()
