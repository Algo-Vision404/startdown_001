# node.py

import asyncio
import logging
import time

import websockets

from chain import Blockchain, ChainError
from block import Block
from Transaction import Transaction
from storage import ChainStore
from peer_manager import PeerManager, PeerState
from message import (
    MessageType,
    serialize_transaction,
    deserialize_transaction,
    serialize_block,
    deserialize_block,
    build,
    parse
)

from utxo import UTXOSet


class Node:

    BLOCK_SIZE         = 2
    MEMPOOL_MAX_SIZE   = 500    # maximum pending transactions kept in the mempool
    PEER_REFRESH_SEC   = 60     # how often to run peer discovery
    PEER_SAVE_SEC      = 120    # how often to save peer list to disk
    PING_INTERVAL_SEC  = 30     # how often to ping connected peers

    # Minimum time between REQUEST_CHAIN broadcasts triggered by an
    # incoming competing/ahead block. Without this, a peer can send an
    # endless stream of uniquely-hashed garbage "blocks" -- each one
    # cheap to produce, none of them needing valid PoW or a real chain
    # behind them -- and each one would trigger a broadcast to every
    # connected peer at zero cost to the sender. See _on_block().
    CHAIN_REQUEST_COOLDOWN_SEC = 5.0

    def __init__(
        self,
        host       : str,
        port       : int,
        data_dir   : str  = "data",
        bootstrap  : list = None   # list of {"host": str, "port": int}
    ):
        self.host          = host
        self.port          = port
        self.bootstrap     = bootstrap or []
        self.store         = ChainStore(port, data_dir)
        self.peer_mgr      = PeerManager(host, port, data_dir)
        self.chain         = Blockchain()
        self.mempool       : list[Transaction] = []
        self.seen_tx_ids   : set = set()
        self.seen_block_hashes : set = set()

        # (tx_id, index) keys currently spent by a pending mempool transaction.
        # Used to reject a new transaction that conflicts with one already
        # sitting in the mempool (a double-spend attempt before confirmation).
        self.pending_inputs : set = set()

        # port -> websocket for active connections
        self.peers         : dict[int, any] = {}

        self.mining        = False
        self.miner_address = "MINER_UNSET"
        self._server       = None

        # Last time a REQUEST_CHAIN broadcast was sent in response to a
        # competing/ahead block, so repeated fake blocks from one peer
        # can't cheaply trigger repeated broadcasts to everyone we know.
        self._last_chain_request_time = 0.0

    # ─────────────────────────────────────────────────────────
    # Startup
    # ─────────────────────────────────────────────────────────

    async def serve(self):
        """
        Load chain from disk, start websocket server,
        then seed the peer manager with bootstrap nodes.
        """
        restored = self.store.load()
        if restored is not None:
            self.chain = restored
            for block in self.chain.chain:
                self.seen_block_hashes.add(block.hash)
            logging.info(
                f"[{self.port}] restored chain "
                f"height={self.chain.height()} "
                f"utxos={self.chain.utxo_set.size()}"
            )
        else:
            logging.info(f"[{self.port}] starting from genesis")

        # Add bootstrap nodes to peer manager
        for b in self.bootstrap:
            self.peer_mgr.add(b["host"], b["port"])

        self._server = await websockets.serve(
            self._handle_incoming,
            self.host,
            self.port
        )
        logging.info(f"[{self.port}] server listening on {self.host}:{self.port}")

    async def start_background_tasks(self):
        """
        Start all periodic background coroutines.
        Must be called after serve().
        """
        asyncio.create_task(self._peer_discovery_loop())
        asyncio.create_task(self._peer_save_loop())
        asyncio.create_task(self._ping_loop())

    def close(self):
        if self._server:
            self._server.close()
        self.peer_mgr.save()

    # ─────────────────────────────────────────────────────────
    # Persistence
    # ─────────────────────────────────────────────────────────

    def _save_chain(self) -> None:
        try:
            self.store.save(self.chain)
        except Exception as e:
            logging.error(f"[{self.port}] chain save failed: {e}")

    # ─────────────────────────────────────────────────────────
    # Peer discovery loop
    # ─────────────────────────────────────────────────────────

    async def _peer_discovery_loop(self):
        """
        Runs forever. Every PEER_REFRESH_SEC seconds:
            1. If below MIN_PEERS, try to connect to known candidates.
            2. Ask connected peers for their peer lists (GET_PEERS).
            3. Connect to newly learned peers if still below MAX_PEERS.

        This is the core of gossip-based peer discovery. No central
        directory server is needed. Each node learns about new nodes
        by asking the nodes it already knows.
        """
        # Initial connection — run immediately on startup
        await self._connect_to_candidates()
        await self._request_peers_from_all()

        while True:
            await asyncio.sleep(self.PEER_REFRESH_SEC)

            await self._connect_to_candidates()

            if not self.peer_mgr.at_capacity():
                await self._request_peers_from_all()

            logging.debug(
                f"[{self.port}] peer discovery tick: "
                f"{self.peer_mgr}"
            )

    async def _peer_save_loop(self):
        """Save peer list to disk periodically."""
        while True:
            await asyncio.sleep(self.PEER_SAVE_SEC)
            self.peer_mgr.save()
            logging.debug(f"[{self.port}] peer list saved")

    async def _ping_loop(self):
        """
        Ping all connected peers periodically to detect dead connections.
        A peer that does not respond within 10 seconds is considered dead
        and its websocket is closed.
        """
        while True:
            await asyncio.sleep(self.PING_INTERVAL_SEC)
            dead = []
            for port, ws in list(self.peers.items()):
                try:
                    await asyncio.wait_for(
                        ws.send(build(MessageType.PING, self.port)),
                        timeout=10.0
                    )
                except Exception:
                    dead.append(port)

            for port in dead:
                logging.warning(
                    f"[{self.port}] peer {port} did not respond to ping"
                )
                await self._disconnect_peer(port)

    # ─────────────────────────────────────────────────────────
    # Connection management
    # ─────────────────────────────────────────────────────────

    async def _connect_to_candidates(self):
        """
        Try to establish outbound connections to known peer candidates
        until we reach MAX_PEERS connections.
        """
        if self.peer_mgr.at_capacity():
            return

        candidates = self.peer_mgr.candidates_to_connect()

        for peer_info in candidates:
            if self.peer_mgr.at_capacity():
                break

            # Skip if already connected by port
            if peer_info.port in self.peers:
                self.peer_mgr.mark_connected(peer_info.host, peer_info.port)
                continue

            await self._connect_to(peer_info.host, peer_info.port)

    async def _connect_to(self, host: str, port: int) -> bool:
        """
        Open a websocket connection to a single peer.

        On success:
            - Registers the websocket in self.peers
            - Marks the peer as CONNECTED in the peer manager
            - Sends a HANDSHAKE
            - Spawns a listener task

        On failure:
            - Marks the peer as FAILED in the peer manager
            - Returns False

        Returns True if connection succeeded.
        """
        uri = f"ws://{host}:{port}"
        try:
            ws = await asyncio.wait_for(
                websockets.connect(uri),
                timeout=5.0
            )
            self.peers[port] = ws
            self.peer_mgr.mark_connected(host, port)

            # Introduce ourselves
            await ws.send(build(
                MessageType.HANDSHAKE,
                self.port,
                {"host": self.host, "port": self.port}
            ))

            # Request peer list immediately
            await ws.send(build(MessageType.GET_PEERS, self.port))

            # Request chain if we might be behind
            await ws.send(build(MessageType.REQUEST_CHAIN, self.port))

            asyncio.create_task(self._listen_to_peer(ws, host, port))

            logging.info(f"[{self.port}] connected to {host}:{port}")
            return True

        except Exception as e:
            self.peer_mgr.mark_failed(host, port)
            logging.debug(f"[{self.port}] could not reach {host}:{port}: {e}")
            return False

    async def _disconnect_peer(self, port: int):
        """Close and clean up a peer connection."""
        ws = self.peers.pop(port, None)
        if ws:
            try:
                await ws.close()
            except Exception:
                pass

        # Find the host for this port in the peer manager
        for peer_info in self.peer_mgr.all_peers():
            if peer_info.port == port:
                self.peer_mgr.mark_disconnected(peer_info.host, port)
                break

    async def _request_peers_from_all(self):
        """Send GET_PEERS to every connected peer."""
        msg = build(MessageType.GET_PEERS, self.port)
        await self._broadcast(msg)

    async def _penalize_peer(self, sender_port: int, reason: str) -> None:
        """
        Record a strike against a peer for sending structurally
        malformed data (not just data we happen to disagree with --
        see the callers for why this is scoped to malformed, not
        merely invalid-per-our-current-state, data). Reuses the
        existing connection-failure tracking in PeerManager, so a
        peer sending enough bad messages gets banned the same way one
        that keeps failing to connect does. If that tips them over
        into BANNED, disconnect them too -- otherwise they'd stay
        actively connected and able to keep sending more garbage
        despite being banned from future reconnection.
        """
        if not sender_port:
            return
        for peer_info in self.peer_mgr.all_peers():
            if peer_info.port == sender_port:
                logging.warning(
                    f"[{self.port}] penalizing {peer_info.host}:"
                    f"{sender_port} for {reason}"
                )
                self.peer_mgr.mark_failed(peer_info.host, sender_port)
                if peer_info.state == PeerState.BANNED:
                    await self._disconnect_peer(sender_port)
                break

    # ─────────────────────────────────────────────────────────
    # Connection handlers
    # ─────────────────────────────────────────────────────────

    async def _handle_incoming(self, websocket):
        """Handle a new inbound websocket connection."""
        async for raw in websocket:
            await self._process(raw, websocket)

    async def _listen_to_peer(self, websocket, host: str, port: int):
        """Read messages from an outgoing peer connection."""
        try:
            async for raw in websocket:
                await self._process(raw, websocket)
        except websockets.exceptions.ConnectionClosed:
            logging.info(f"[{self.port}] connection to {host}:{port} closed")
        except Exception as e:
            logging.warning(f"[{self.port}] error with {host}:{port}: {e}")
        finally:
            self.peers.pop(port, None)
            self.peer_mgr.mark_disconnected(host, port)

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
            await self._on_handshake(sender_port, data, websocket)

        elif msg_type == MessageType.GET_PEERS:
            # Respond with our known peer list
            peers_data = self.peer_mgr.shareable_peers()
            await websocket.send(
                build(MessageType.PEERS, self.port, peers_data)
            )

        elif msg_type == MessageType.PEERS:
            if data:
                await self._on_peers(data)

        elif msg_type == MessageType.TRANSACTION:
            if data:
                try:
                    tx = deserialize_transaction(data)
                except Exception as e:
                    logging.warning(
                        f"[{self.port}] rejected malformed transaction: {e}"
                    )
                    await self._penalize_peer(sender_port, "malformed transaction")
                else:
                    await self._on_transaction(tx)

        elif msg_type == MessageType.BLOCK:
            if data:
                try:
                    block = deserialize_block(data)
                except Exception as e:
                    logging.warning(
                        f"[{self.port}] rejected malformed block: {e}"
                    )
                    await self._penalize_peer(sender_port, "malformed block")
                else:
                    await self._on_block(block)

        elif msg_type == MessageType.REQUEST_CHAIN:
            chain_data = [serialize_block(b) for b in self.chain.chain]
            await websocket.send(
                build(MessageType.CHAIN, self.port, chain_data)
            )

        elif msg_type == MessageType.CHAIN:
            if data:
                await self._on_chain(data, sender_port)

        elif msg_type == MessageType.PING:
            await websocket.send(build(MessageType.PONG, self.port))

        elif msg_type == MessageType.PONG:
            if sender_port:
                for peer_info in self.peer_mgr.all_peers():
                    if peer_info.port == sender_port:
                        self.peer_mgr.mark_seen(peer_info.host, sender_port)
                        break

    # ─────────────────────────────────────────────────────────
    # Peer message handlers
    # ─────────────────────────────────────────────────────────

    async def _on_handshake(self, sender_port: int, data: dict, websocket):
        """
        Register a peer that just connected to us.

        The HANDSHAKE message now includes the sender's host and port
        so we can add them to our peer manager and potentially connect
        back to them (bidirectional peer exchange).
        """
        if not sender_port:
            return

        host = None
        if data and isinstance(data, dict):
            host = data.get("host")
            port = data.get("port", sender_port)
        else:
            host = "localhost"
            port = sender_port

        if host:
            self.peer_mgr.add(host, port)
            self.peer_mgr.mark_connected(host, port)

        if sender_port not in self.peers:
            self.peers[sender_port] = websocket

        logging.info(f"[{self.port}] handshake from {host}:{sender_port}")

    async def _on_peers(self, peer_list: list):
        """
        Process a PEERS message.

        Add newly learned peers to our peer manager.
        If we are below MIN_PEERS, immediately try to connect to them.
        """
        added = self.peer_mgr.add_many(peer_list)

        if added > 0:
            logging.info(
                f"[{self.port}] learned {added} new peers "
                f"from PEERS message"
            )

        if self.peer_mgr.needs_peers():
            await self._connect_to_candidates()

    # ─────────────────────────────────────────────────────────
    # Mempool helpers
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _input_keys(tx: Transaction) -> set:
        if tx.is_coinbase:
            return set()
        return {(i["tx_id"], i["index"]) for i in tx.inputs}

    def _recompute_pending_inputs(self) -> None:
        """
        Rebuild self.pending_inputs from the current mempool contents.

        Called whenever the mempool is filtered (a block confirmed some
        of its transactions, locally or from a peer). This keeps the
        mempool-level double-spend guard in sync with what is actually
        still pending, without needing to track removals one at a time.
        """
        pending = set()
        for tx in self.mempool:
            pending |= self._input_keys(tx)
        self.pending_inputs = pending

    def _evict_excess_mempool(self) -> None:
        """
        Enforce MEMPOOL_MAX_SIZE by dropping the lowest-fee transactions.

        The mempool is kept sorted by fee (descending) by the caller, so
        the transactions to evict are always the tail of the list. This
        bounds memory use under a flood of low-fee or spam transactions:
        once the mempool is full, only a transaction paying a higher fee
        than the current lowest-fee entry can displace it.

        Evicted transactions are removed from seen_tx_ids as well as the
        mempool, so they are not permanently blacklisted — a later
        resubmission (e.g. with a bumped fee) is treated as new.
        """
        if len(self.mempool) <= self.MEMPOOL_MAX_SIZE:
            return

        evicted      = self.mempool[self.MEMPOOL_MAX_SIZE:]
        self.mempool = self.mempool[:self.MEMPOOL_MAX_SIZE]

        for tx in evicted:
            self.seen_tx_ids.discard(tx.tx_id)

        self._recompute_pending_inputs()

        logging.info(
            f"[{self.port}] evicted {len(evicted)} low-fee mempool "
            f"transactions (cap={self.MEMPOOL_MAX_SIZE})"
        )

    # ─────────────────────────────────────────────────────────
    # Transaction handling
    # ─────────────────────────────────────────────────────────

    async def _on_transaction(self, tx: Transaction):
        if tx.tx_id in self.seen_tx_ids:
            return

        if tx.is_coinbase:
            # A coinbase transaction is only ever valid as the first
            # transaction of a mined block (see chain.py's mine_block(),
            # which builds it internally and explicitly refuses one
            # submitted this way). Accepting one here would be worse
            # than merely wrong: the block below that skips UTXO
            # validation "if not tx.is_coinbase" would let it straight
            # into the mempool, and mine_block() raises the moment it's
            # ever included in a to-be-mined batch -- so a single
            # crafted message would permanently jam mining on any node
            # whose mempool includes it, without any UTXO check ever
            # having to catch it (there's no input to check). Reject
            # it before that block is reached.
            logging.warning(
                f"[{self.port}] rejected transaction claiming to be "
                f"coinbase (coinbase transactions are never accepted "
                f"directly from the network)"
            )
            return

        if not tx.is_valid():
            logging.warning(
                f"[{self.port}] rejected invalid tx {tx.tx_id[:16]}"
            )
            return

        # A coinbase transaction can never reach this point (rejected
        # above), so every transaction here genuinely has inputs to
        # validate against the UTXO set.
        if not tx.validate_against_utxo_set(self.chain.utxo_set):
            logging.warning(
                f"[{self.port}] rejected tx {tx.tx_id[:16]}: "
                f"UTXO validation failed"
            )
            return

        # Reject if this transaction's inputs conflict with a
        # transaction already sitting in the mempool (first-seen wins).
        keys = self._input_keys(tx)
        if keys & self.pending_inputs:
            logging.warning(
                f"[{self.port}] rejected tx {tx.tx_id[:16]}: "
                f"conflicts with a pending mempool transaction "
                f"(double-spend attempt)"
            )
            return

        self.pending_inputs |= keys

        self.seen_tx_ids.add(tx.tx_id)
        self.mempool.append(tx)
        self.mempool.sort(key=lambda t: t.fee, reverse=True)
        self._evict_excess_mempool()

        logging.info(
            f"[{self.port}] accepted tx {tx.tx_id[:16]}... "
            f"fee={tx.fee} mempool={len(self.mempool)}"
        )

        await self._broadcast(
            build(MessageType.TRANSACTION, self.port,
                  serialize_transaction(tx))
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

        if (block.previous_hash == last.hash
                and block.index == last.index + 1):

            expected_diff = self.chain.expected_difficulty(self.chain.chain)
            if block.difficulty != expected_diff:
                logging.warning(
                    f"[{self.port}] block {block.index} has wrong "
                    f"difficulty: expected {expected_diff}, "
                    f"got {block.difficulty}"
                )
                return

            if not block.hash.startswith("0" * block.difficulty):
                logging.warning(
                    f"[{self.port}] block {block.index} fails PoW"
                )
                return

            if not block.is_internally_valid():
                logging.warning(
                    f"[{self.port}] block {block.index} fails integrity"
                )
                return

            if not self.chain.coinbase_reward_is_valid(block):
                logging.warning(
                    f"[{self.port}] block {block.index} has an invalid "
                    f"coinbase reward"
                )
                return

            for tx in block.transactions:
                if not tx.is_valid():
                    logging.warning(
                        f"[{self.port}] block {block.index} "
                        f"invalid signature"
                    )
                    return

            # Validate the whole transaction set together. Per-transaction
            # signature checks above do not catch two transactions in the
            # same block spending the same input (a double-spend) — this
            # does, by replaying the block's transactions against the
            # confirmed UTXO set in order.
            if not Blockchain.validate_transaction_sequence(
                block.transactions, self.chain.utxo_set
            ):
                logging.warning(
                    f"[{self.port}] block {block.index} rejected: "
                    f"conflicting/double-spend transactions"
                )
                return

            self.chain.append_block(block)
            self._save_chain()

            confirmed_ids = {tx.tx_id for tx in block.transactions}
            self.mempool  = [
                tx for tx in self.mempool
                if tx.tx_id not in confirmed_ids
            ]
            self._recompute_pending_inputs()

            logging.info(
                f"[{self.port}] appended block {block.index} "
                f"hash={block.hash[:16]}... "
                f"height={self.chain.height()} "
                f"utxos={self.chain.utxo_set.size()}"
            )

            await self._broadcast(
                build(MessageType.BLOCK, self.port, serialize_block(block))
            )

        elif block.index >= last.index:
            # Either we're behind (block.index > last.index + 1), or this
            # block conflicts with our tip -- same index as ours (a fork
            # at our current height) or index == last.index + 1 with a
            # previous_hash that doesn't match (a fork attempt one block
            # ahead). All three used to fall through here silently for
            # the first two cases; now every one of them asks for the
            # full candidate chain so _on_chain can decide by cumulative
            # work rather than just dropping a legitimate competing block.
            #
            # This request is cooldown-limited (not per-block): a peer
            # can produce an endless stream of uniquely-hashed, cheap
            # "blocks" that reach this branch without needing valid PoW
            # or a real chain behind them (the deeper checks only run
            # once we actually fetch and validate a candidate chain in
            # _on_chain), so without a cooldown here, each one would be
            # a free trigger for a broadcast to every peer we know.
            now = time.time()
            if now - self._last_chain_request_time < self.CHAIN_REQUEST_COOLDOWN_SEC:
                return
            self._last_chain_request_time = now

            logging.info(
                f"[{self.port}] observed a competing or ahead block "
                f"(index={block.index}, ours={last.index}), "
                f"requesting chain to compare work"
            )
            await self._broadcast(
                build(MessageType.REQUEST_CHAIN, self.port)
            )

    # ─────────────────────────────────────────────────────────
    # Chain sync
    # ─────────────────────────────────────────────────────────

    async def _on_chain(self, chain_data: list, sender_port: int = None):
        try:
            candidate_blocks = [deserialize_block(b) for b in chain_data]
        except Exception as e:
            logging.warning(
                f"[{self.port}] rejected malformed chain: {e}"
            )
            await self._penalize_peer(sender_port, "malformed chain")
            return

        candidate          = Blockchain.__new__(Blockchain)
        candidate.chain    = candidate_blocks
        candidate.utxo_set = UTXOSet()

        if not candidate.chain or candidate.chain[0].hash != self.chain.chain[0].hash:
            logging.warning(
                f"[{self.port}] rejected chain with an unknown genesis block"
            )
            return

        # Compare total proof-of-work, not block count. With difficulty
        # now varying over time (see chain.py's retargeting), a longer
        # chain of easier blocks is not necessarily the one representing
        # more real mining effort -- cumulative_work() is what fork
        # resolution should actually be deciding on. This also means we
        # can cheaply reject a weaker candidate before paying for the
        # full is_valid() replay below.
        candidate_work = Blockchain.cumulative_work(candidate.chain)
        current_work   = Blockchain.cumulative_work(self.chain.chain)

        if candidate_work <= current_work:
            return

        for block in candidate.chain:
            candidate.utxo_set.apply_block(block)

        if candidate.is_valid():
            old_height = self.chain.height()
            self.chain = candidate
            self._save_chain()

            # Drop any mempool transactions confirmed on the adopted chain,
            # and resync the double-spend guard to the new chain state.
            confirmed_ids = {
                tx.tx_id
                for block in self.chain.chain
                for tx in block.transactions
            }
            self.mempool = [
                tx for tx in self.mempool
                if tx.tx_id not in confirmed_ids
            ]
            self._recompute_pending_inputs()

            logging.info(
                f"[{self.port}] adopted a chain with more work "
                f"(height {old_height} -> {self.chain.height()}, "
                f"work {current_work} -> {candidate_work})"
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
                to_mine,
                self.miner_address
            )

            if block.previous_hash != self.chain.chain[-1].hash:
                logging.info(f"[{self.port}] mined block stale, discarding")
                return

            self.chain.append_block(block)
            self.seen_block_hashes.add(block.hash)
            self._save_chain()

            mined_ids    = {tx.tx_id for tx in to_mine}
            self.mempool = [
                tx for tx in self.mempool
                if tx.tx_id not in mined_ids
            ]
            self._recompute_pending_inputs()

            reward = (
                block.transactions[0].total_output()
                if block.transactions else 0
            )

            logging.info(
                f"[{self.port}] mined block {block.index} "
                f"nonce={block.nonce} reward={reward} "
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
        for port, ws in list(self.peers.items()):
            try:
                await ws.send(message)
            except Exception:
                dead.append(port)
        for port in dead:
            await self._disconnect_peer(port)

    # ─────────────────────────────────────────────────────────
    # Public API used by api.py and cli.py
    # ─────────────────────────────────────────────────────────

    def balance(self, address: str) -> float:
        return self.chain.utxo_set.balance(address)

    def utxos_for(self, address: str) -> list:
        return [
            u.to_dict()
            for u in self.chain.utxo_set.utxos_for(address)
        ]

    def status(self) -> dict:
        peer_summary = self.peer_mgr.summary()
        return {
            "port"           : self.port,
            "host"           : self.host,
            "height"         : self.chain.height(),
            "tip_hash"       : self.chain.chain[-1].hash[:32],
            "difficulty"     : self.chain.chain[-1].difficulty,
            "chain_valid"    : self.chain.is_valid(),
            "mempool"        : len(self.mempool),
            "peers"          : list(self.peers.keys()),
            "peer_summary"   : peer_summary,
            "mining"         : self.mining,
            "miner_address"  : self.miner_address
        }

    def __repr__(self) -> str:
        return (
            f"Node(port={self.port}, "
            f"height={self.chain.height()}, "
            f"utxos={self.chain.utxo_set.size()}, "
            f"peers={self.peer_mgr.summary()})"
        )