# peer_manager.py
#
# Peer lifecycle management.
#
# Responsibilities:
#   - Track all known peers (address, port, state)
#   - Decide which peers to connect to
#   - Score peers by reliability (successful connections, uptime)
#   - Enforce connection limits
#   - Persist the peer list to disk so a restarted node
#     does not need to rediscover everyone from scratch
#
# Peer states:
#   KNOWN       -- we know about this peer but are not connected
#   CONNECTED   -- active websocket connection exists
#   FAILED      -- last connection attempt failed
#   BANNED      -- peer sent invalid data, do not reconnect
#
# Connection limits:
#   MAX_PEERS       -- maximum simultaneous outbound connections
#   MIN_PEERS       -- trigger rediscovery if below this number
#   MAX_KNOWN       -- maximum size of the known peer list
#
# A peer is identified by (host, port). Two nodes on the same machine
# use different ports. On separate machines they use different hosts.

import json
import os
import time
import logging

from dataclasses import dataclass, field
from enum import Enum


class PeerState(Enum):
    KNOWN     = "known"
    CONNECTED = "connected"
    FAILED    = "failed"
    BANNED    = "banned"


@dataclass
class PeerInfo:
    host           : str
    port           : int
    state          : PeerState = PeerState.KNOWN
    score          : int       = 0       # higher = more reliable
    last_seen      : float     = 0.0     # unix timestamp
    last_attempted : float     = 0.0     # unix timestamp of last connect attempt
    fail_count     : int       = 0       # consecutive failed connections

    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def to_dict(self) -> dict:
        return {
            "host"           : self.host,
            "port"           : self.port,
            "state"          : self.state.value,
            "score"          : self.score,
            "last_seen"      : self.last_seen,
            "last_attempted" : self.last_attempted,
            "fail_count"     : self.fail_count
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PeerInfo":
        peer = cls(
            host           = data["host"],
            port           = data["port"],
            score          = data.get("score", 0),
            last_seen      = data.get("last_seen", 0.0),
            last_attempted = data.get("last_attempted", 0.0),
            fail_count     = data.get("fail_count", 0)
        )
        state_str = data.get("state", "known")
        try:
            peer.state = PeerState(state_str)
        except ValueError:
            peer.state = PeerState.KNOWN
        # Restore connected peers as known on load.
        # The connection does not survive a restart.
        if peer.state == PeerState.CONNECTED:
            peer.state = PeerState.KNOWN
        return peer


class PeerManager:
    """
    Manages the set of known and connected peers.

    The manager is the single source of truth for peer state.
    The node asks the manager which peers to connect to,
    and reports back connection successes and failures.
    """

    MAX_PEERS  = 8     # maximum outbound connections
    MIN_PEERS  = 2     # rediscover if below this
    MAX_KNOWN  = 200   # cap on known peer list size
    BAN_AFTER  = 5     # ban a peer after this many consecutive failures

    def __init__(self, host: str, port: int, data_dir: str = "data"):
        self.host      = host
        self.port      = port
        self.data_dir  = data_dir
        self._peers    : dict[str, PeerInfo] = {}  # address -> PeerInfo
        self._filepath = os.path.join(data_dir, f"peers_{port}.json")
        os.makedirs(data_dir, exist_ok=True)
        self._load()

    # ─────────────────────────────────────────────────────────
    # Peer registration
    # ─────────────────────────────────────────────────────────

    def add(self, host: str, port: int) -> bool:
        """
        Add a peer to the known set.

        Returns True if the peer is new, False if already known.
        Does not add ourselves (same host+port as this node).
        Does not add banned peers.
        Does not exceed MAX_KNOWN.
        """
        if host == self.host and port == self.port:
            return False

        address = f"{host}:{port}"

        if address in self._peers:
            return False

        if len(self._peers) >= self.MAX_KNOWN:
            self._evict_worst()

        self._peers[address] = PeerInfo(host=host, port=port)
        logging.debug(f"[peer_mgr:{self.port}] learned peer {address}")
        return True

    def add_many(self, peer_list: list) -> int:
        """
        Add multiple peers from a received PEERS message.
        peer_list: [{"host": str, "port": int}, ...]
        Returns the number of new peers added.
        """
        added = 0
        for entry in peer_list:
            try:
                if self.add(entry["host"], int(entry["port"])):
                    added += 1
            except (KeyError, ValueError, TypeError):
                continue
        return added

    def mark_connected(self, host: str, port: int) -> None:
        address = f"{host}:{port}"
        if address in self._peers:
            peer            = self._peers[address]
            peer.state      = PeerState.CONNECTED
            peer.last_seen  = time.time()
            peer.fail_count = 0
            peer.score      += 1

    def mark_disconnected(self, host: str, port: int) -> None:
        address = f"{host}:{port}"
        if address in self._peers:
            peer = self._peers[address]
            if peer.state == PeerState.CONNECTED:
                peer.state = PeerState.KNOWN

    def mark_failed(self, host: str, port: int) -> None:
        address = f"{host}:{port}"
        if address in self._peers:
            peer                = self._peers[address]
            peer.state          = PeerState.FAILED
            peer.last_attempted = time.time()
            peer.fail_count     += 1
            peer.score          = max(0, peer.score - 1)

            if peer.fail_count >= self.BAN_AFTER:
                peer.state = PeerState.BANNED
                logging.warning(
                    f"[peer_mgr:{self.port}] banned {address} "
                    f"after {peer.fail_count} failures"
                )

    def mark_seen(self, host: str, port: int) -> None:
        address = f"{host}:{port}"
        if address in self._peers:
            self._peers[address].last_seen = time.time()

    def ban(self, host: str, port: int) -> None:
        address = f"{host}:{port}"
        if address in self._peers:
            self._peers[address].state = PeerState.BANNED
            logging.warning(f"[peer_mgr:{self.port}] banned {address}")

    # ─────────────────────────────────────────────────────────
    # Peer selection
    # ─────────────────────────────────────────────────────────

    def candidates_to_connect(self) -> list[PeerInfo]:
        """
        Return peers that are worth trying to connect to right now.

        Criteria:
            - State is KNOWN or FAILED (not CONNECTED, not BANNED)
            - Last attempt was more than 60 seconds ago
              (avoid hammering a peer that keeps failing)
            - Sorted by score descending (try reliable peers first)
        """
        now       = time.time()
        cooldown  = 60.0

        eligible = [
            p for p in self._peers.values()
            if p.state in (PeerState.KNOWN, PeerState.FAILED)
            and now - p.last_attempted > cooldown
        ]

        eligible.sort(key=lambda p: p.score, reverse=True)
        return eligible

    def connected_count(self) -> int:
        return sum(
            1 for p in self._peers.values()
            if p.state == PeerState.CONNECTED
        )

    def needs_peers(self) -> bool:
        return self.connected_count() < self.MIN_PEERS

    def at_capacity(self) -> bool:
        return self.connected_count() >= self.MAX_PEERS

    def connected_peers(self) -> list[PeerInfo]:
        return [p for p in self._peers.values() if p.state == PeerState.CONNECTED]

    def all_peers(self) -> list[PeerInfo]:
        return list(self._peers.values())

    def shareable_peers(self) -> list[dict]:
        """
        Return a list of peers suitable for sharing in a PEERS message.

        Only share peers we have actually connected to successfully
        (score > 0) so we do not propagate unverified addresses.
        Exclude banned and failed peers.
        Cap at 50 entries to keep messages small.
        """
        good = [
            p for p in self._peers.values()
            if p.state in (PeerState.KNOWN, PeerState.CONNECTED)
            and p.score > 0
        ]
        good.sort(key=lambda p: p.score, reverse=True)
        return [
            {"host": p.host, "port": p.port}
            for p in good[:50]
        ]

    # ─────────────────────────────────────────────────────────
    # Housekeeping
    # ─────────────────────────────────────────────────────────

    def _evict_worst(self) -> None:
        """
        Remove the lowest-scoring non-connected peer to make room.
        """
        candidates = [
            p for p in self._peers.values()
            if p.state != PeerState.CONNECTED
        ]
        if candidates:
            worst = min(candidates, key=lambda p: p.score)
            del self._peers[worst.address()]

    def summary(self) -> dict:
        counts = {state: 0 for state in PeerState}
        for p in self._peers.values():
            counts[p.state] += 1
        return {
            "total"     : len(self._peers),
            "connected" : counts[PeerState.CONNECTED],
            "known"     : counts[PeerState.KNOWN],
            "failed"    : counts[PeerState.FAILED],
            "banned"    : counts[PeerState.BANNED]
        }

    # ─────────────────────────────────────────────────────────
    # Persistence
    # ─────────────────────────────────────────────────────────

    def save(self) -> None:
        """
        Persist the peer list to disk.

        Called periodically and on shutdown so a restarting node
        can reconnect to known peers without going back to the
        bootstrap node every time.

        Banned peers are saved so we do not forget the ban after restart.
        CONNECTED peers are saved as KNOWN since connections do not
        survive restarts.
        """
        data = [p.to_dict() for p in self._peers.values()]
        tmp  = self._filepath + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self._filepath)

    def _load(self) -> None:
        if not os.path.exists(self._filepath):
            return
        try:
            with open(self._filepath, "r") as f:
                data = json.load(f)
            for entry in data:
                peer = PeerInfo.from_dict(entry)
                # Skip banned peers older than 24 hours
                if (peer.state == PeerState.BANNED
                        and time.time() - peer.last_attempted > 86400):
                    continue
                self._peers[peer.address()] = peer
            logging.info(
                f"[peer_mgr:{self.port}] loaded "
                f"{len(self._peers)} peers from disk"
            )
        except Exception as e:
            logging.warning(
                f"[peer_mgr:{self.port}] could not load peers: {e}"
            )

    def __repr__(self) -> str:
        s = self.summary()
        return (
            f"PeerManager(port={self.port}, "
            f"connected={s['connected']}, "
            f"known={s['known']}, "
            f"total={s['total']})"
        )