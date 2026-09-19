# node.py
#
# A single network node with persistent chain storage.
#
# Changes from week 5-6:
#   - ChainStore is created in __init__ and used to restore state on startup.
#   - _save_chain() is called after every block append.
#   - serve() loads from disk before starting the server.

import asyncio
import logging

import websockets

from chain import Blockchain, ChainError
from block import Block
from Transaction import Transaction
from storage import ChainStore
from message import (
    MessageType,
    serialize_transaction,
    deserialize_transaction,
    serialize_block,
    deserialize_block,
    build,
    parse
)


class Node:

    BLOCK_SIZE = 2

    def __init__(self, port: int, peer_ports: list, data_dir: str = "data"):
        self.port              = port
        self.peer_ports        = peer_ports
        self.store             = ChainStore(port, data_dir)
        self.chain             = Blockchain()
        self.mempool           : list[Transaction] = []
        self.seen_tx_ids       : set = set()
        self.seen_block_hashes : set = set()
        self.peers             : dict = {}
        self.mining            = False
        self._server           = None

    # ─────────────────────────────────────────────────────────
    # Startup
    # ─────────────────────────────────────────────────────────

    async def serve(self):
        """
        Restore chain from disk if available, then start the server.
        """
        restored = self.store.load()
        if restored is not None:
            self.chain = restored

            # Populate seen_block_hashes from the restored chain so
            # we do not re-process blocks we already have.
            for block in self.chain.chain:
                self.seen_block_hashes.add(block.hash)

            logging.info(
                f"[{self.port}] restored chain from disk "
                f"height={self.chain.height()}"
            )
        else:
            logging.info(f"[{self.port}] no stored chain found, starting from genesis")

        self._server = await websockets.serve(
            self._handle_incoming,
            "localhost",
            self.port
        )
        logging.info(f"[{self.port}] server listening")

    async def connect_to_peers(self):
        for peer_port in self.peer_ports:
            try:
                ws = await websockets.connect(f"ws://localhost:{peer_port}")
                self.peers[peer_port] = ws
                asyncio.create_task(self._listen_to_peer(ws, peer_port))
                await ws.send(build(MessageType.HANDSHAKE, self.port))
                logging.info(f"[{self.port}] connected to peer {peer_port}")
            except Exception as e:
                logging.warning(f"[{self.port}] could not reach {peer_port}: {e}")

    def close(self):
        if self._server:
            self._server.close()

    # ─────────────────────────────────────────────────────────
    # Persistence
    # ─────────────────────────────────────────────────────────

    def _save_chain(self) -> None:
        """
        Write the current chain to disk.
        Called after every successful block append.
        Errors are logged but do not crash the node —
        a failed save is recoverable on next startup via chain sync.
        """
        try:
            self.store.save(self.chain)
        except Exception as e:
            logging.error(f"[{self.port}] chain save failed: {e}")

    # ─────────────────────────────────────────────────────────
    # Connection handlers
    # ─────────────────────────────────────────────────────────

    async def _handle_incoming(self, websocket):
        async for raw in websocket:
            await self._process(raw, websocket)

    async def _listen_to_peer(self, websocket, peer_port: int):
        try:
            async for raw in websocket:
                await self._process(raw, websocket)
        except websockets.exceptions.ConnectionClosed:
            logging.warning(f"[{self.port}] connection to {peer_port} closed")
        finally:
            self.peers.pop(peer_port, None)

    # ─────────────────────────────────────────────────────────
    # Message dispatch
    # ─────────────────────────────────────────────────────────

    async def _process(self, raw: str, websocket):
        try:
            msg = parse(raw)
        except Exception:
            return

        msg_type    = msg.get("type")
        sender_port = msg.get("sender_port")
        data        = msg.get("data")

        if msg_type == MessageType.HANDSHAKE:
            if sender_port and sender_port not in self.peers:
                self.peers[sender_port] = websocket
                logging.info(f"[{self.port}] registered peer {sender_port}")

        elif msg_type == MessageType.TRANSACTION:
            if data:
                tx = deserialize_transaction(data)
                await self._on_transaction(tx)

        elif msg_type == MessageType.BLOCK:
            if data:
                block = deserialize_block(data)
                await self._on_block(block)

        elif msg_type == MessageType.REQUEST_CHAIN:
            chain_data = [serialize_block(b) for b in self.chain.chain]
            await websocket.send(build(MessageType.CHAIN, self.port, chain_data))

        elif msg_type == MessageType.CHAIN:
            if data:
                await self._on_chain(data)

    # ─────────────────────────────────────────────────────────
    # Transaction handling
    # ─────────────────────────────────────────────────────────

    async def _on_transaction(self, tx: Transaction):
        if tx.tx_id in self.seen_tx_ids:
            return

        if not tx.is_valid():
            logging.warning(f"[{self.port}] rejected invalid tx {tx.tx_id[:16]}")
            return

        self.seen_tx_ids.add(tx.tx_id)
        self.mempool.append(tx)

        logging.info(
            f"[{self.port}] accepted tx {tx.tx_id[:16]}... "
            f"mempool={len(self.mempool)}"
        )

        await self._broadcast(
            build(MessageType.TRANSACTION, self.port, serialize_transaction(tx))
        )

        if len(self.mempool) >= self.BLOCK_SIZE and not self.mining:
            asyncio.create_task(self._mine())

    async def submit_transaction(self, tx: Transaction):
        await self._on_transaction(tx)

    # ─────────────────────────────────────────────────────────
    # Block handling
    # ─────────────────────────────────────────────────────────

    async def _on_block(self, block: Block):
        if block.hash in self.seen_block_hashes:
            return

        self.seen_block_hashes.add(block.hash)

        last = self.chain.chain[-1]

        if block.previous_hash == last.hash and block.index == last.index + 1:

            if not block.hash.startswith("0" * Blockchain.DIFFICULTY):
                logging.warning(f"[{self.port}] block {block.index} fails proof-of-work")
                return

            if not block.is_internally_valid():
                logging.warning(f"[{self.port}] block {block.index} fails integrity check")
                return

            for tx in block.transactions:
                if not tx.is_valid():
                    logging.warning(f"[{self.port}] block {block.index} invalid signature")
                    return

            self.chain.append_block(block)
            self._save_chain()

            confirmed_ids = {tx.tx_id for tx in block.transactions}
            self.mempool  = [
                tx for tx in self.mempool
                if tx.tx_id not in confirmed_ids
            ]

            logging.info(
                f"[{self.port}] appended block {block.index} "
                f"hash={block.hash[:16]}... "
                f"height={self.chain.height()}"
            )

            await self._broadcast(
                build(MessageType.BLOCK, self.port, serialize_block(block))
            )

        elif block.index > last.index + 1:
            logging.info(f"[{self.port}] behind, requesting chain")
            await self._broadcast(build(MessageType.REQUEST_CHAIN, self.port))

    # ─────────────────────────────────────────────────────────
    # Chain sync
    # ─────────────────────────────────────────────────────────

    async def _on_chain(self, chain_data: list):
        if len(chain_data) <= len(self.chain.chain):
            return

        candidate       = Blockchain.__new__(Blockchain)
        candidate.chain = [deserialize_block(b) for b in chain_data]

        if candidate.is_valid():
            old_height  = self.chain.height()
            self.chain  = candidate
            self._save_chain()

            logging.info(
                f"[{self.port}] adopted longer chain "
                f"{old_height} -> {self.chain.height()}"
            )

    # ─────────────────────────────────────────────────────────
    # Mining
    # ─────────────────────────────────────────────────────────

    async def _mine(self):
        self.mining  = True
        tip_hash     = self.chain.chain[-1].hash
        to_mine      = self.mempool[:self.BLOCK_SIZE]

        logging.info(
            f"[{self.port}] mining {len(to_mine)} txs "
            f"tip={tip_hash[:16]}..."
        )

        try:
            loop  = asyncio.get_event_loop()
            block = await loop.run_in_executor(
                None,
                self.chain.mine_block,
                to_mine
            )

            if block.previous_hash != self.chain.chain[-1].hash:
                logging.info(f"[{self.port}] mined block is stale, discarding")
                return

            self.chain.append_block(block)
            self.seen_block_hashes.add(block.hash)
            self._save_chain()

            mined_ids    = {tx.tx_id for tx in to_mine}
            self.mempool = [
                tx for tx in self.mempool
                if tx.tx_id not in mined_ids
            ]

            logging.info(
                f"[{self.port}] mined block {block.index} "
                f"nonce={block.nonce} "
                f"hash={block.hash[:16]}... "
                f"height={self.chain.height()}"
            )

            await self._broadcast(
                build(MessageType.BLOCK, self.port, serialize_block(block))
            )

        except ChainError as e:
            logging.error(f"[{self.port}] mining rejected: {e}")
        except Exception as e:
            logging.error(f"[{self.port}] mining error: {e}")
        finally:
            self.mining = False

    # ─────────────────────────────────────────────────────────
    # Broadcast
    # ─────────────────────────────────────────────────────────

    async def _broadcast(self, message: str):
        dead = []
        for port, ws in self.peers.items():
            try:
                await ws.send(message)
            except Exception:
                dead.append(port)
        for port in dead:
            self.peers.pop(port, None)

    # ─────────────────────────────────────────────────────────
    # Inspection
    # ─────────────────────────────────────────────────────────

    def balance(self, address: str) -> float:
        """
        Compute the balance of an address by walking the full chain.

        This is a naive full-scan. A production implementation would
        maintain a UTXO set or account state trie updated incrementally
        as each block is appended. For a prototype this is sufficient.
        """
        total = 0.0
        for block in self.chain.chain:
            for tx in block.transactions:
                if tx.recipient == address:
                    total += tx.amount
                if tx.sender == address:
                    total -= tx.amount
        return round(total, 8)

    def status(self) -> dict:
        return {
            "port"        : self.port,
            "height"      : self.chain.height(),
            "tip_hash"    : self.chain.chain[-1].hash[:32],
            "chain_valid" : self.chain.is_valid(),
            "mempool"     : len(self.mempool),
            "peers"       : list(self.peers.keys()),
            "mining"      : self.mining
        }

    def __repr__(self) -> str:
        return (
            f"Node(port={self.port}, "
            f"height={self.chain.height()}, "
            f"peers={list(self.peers.keys())})"
        )