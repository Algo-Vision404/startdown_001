# storage.py
#
# Persistent storage for chain state and wallets.
#
# Everything is written to disk as JSON. This means the chain
# survives a process restart. On startup, the node loads its
# previous chain from disk rather than starting from genesis.
#
# Two storage targets:
#
#   ChainStore   -- reads and writes the full blockchain
#   WalletStore  -- reads and writes named wallets
#
# File layout (all relative to a configurable data directory):
#
#   {data_dir}/chain_{port}.json     -- chain for a specific node port
#   {data_dir}/wallets.json          -- all named wallets
#
# Atomicity:
#   Writes go to a temporary file first, then the file is renamed
#   over the target. On all major operating systems, rename is an
#   atomic filesystem operation. This prevents a crash mid-write
#   from leaving a corrupt or partial file on disk.

import json
import os
import base64
import tempfile

from chain import Blockchain
from block import Block
from wallet import QuantumWallet, ALGORITHM
from Transaction import Transaction
from message import (
    serialize_block,
    deserialize_block,
    serialize_transaction,
    deserialize_transaction
)

import oqs


DEFAULT_DATA_DIR = "data"


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _atomic_write(filepath: str, content: str) -> None:
    """
    Write content to filepath atomically.

    Writes to a temp file in the same directory, then renames.
    The rename replaces the target file in a single filesystem
    operation, so a concurrent reader will always see either the
    old complete file or the new complete file, never a partial write.
    """
    dirpath = os.path.dirname(filepath) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dirpath, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp_path, filepath)
    except Exception:
        os.unlink(tmp_path)
        raise


# ─────────────────────────────────────────────────────────────
# Chain storage
# ─────────────────────────────────────────────────────────────

class ChainStore:
    """
    Saves and loads a Blockchain to/from a JSON file.

    Each node has its own chain file identified by its port number.
    On startup, call load() to restore the previous chain state.
    Call save() after every block append to keep the file current.
    """

    def __init__(self, port: int, data_dir: str = DEFAULT_DATA_DIR):
        _ensure_dir(data_dir)
        self.filepath = os.path.join(data_dir, f"chain_{port}.json")

    def save(self, chain: Blockchain) -> None:
        """
        Serialize the full chain to disk.

        Each block is serialized with full transaction data including
        signatures, so the chain can be fully re-validated after loading.
        """
        data = [serialize_block(b) for b in chain.chain]
        _atomic_write(self.filepath, json.dumps(data, indent=2))

    def load(self) -> Blockchain | None:
        """
        Load a chain from disk.

        Returns a Blockchain if a valid file exists, None otherwise.
        Validation is performed after loading — if the stored chain
        fails validation it is discarded and None is returned so the
        caller falls back to starting fresh from genesis.

        Returns None (not an exception) on any failure so callers
        can always fall back to a fresh chain without extra error
        handling.
        """
        if not os.path.exists(self.filepath):
            return None

        try:
            with open(self.filepath, "r") as f:
                data = json.load(f)

            candidate       = Blockchain.__new__(Blockchain)
            candidate.chain = [deserialize_block(b) for b in data]

            if not candidate.is_valid():
                print(f"stored chain at {self.filepath} failed validation, starting fresh")
                return None

            return candidate

        except Exception as e:
            print(f"could not load chain from {self.filepath}: {e}, starting fresh")
            return None

    def exists(self) -> bool:
        return os.path.exists(self.filepath)


# ─────────────────────────────────────────────────────────────
# Wallet storage
# ─────────────────────────────────────────────────────────────

class WalletStore:
    """
    Saves and loads named wallets to/from a single JSON file.

    Wallets are stored as a dict keyed by name:
        {
            "alice" : { "algorithm": ..., "address": ..., "public_key": ..., "private_key": ... },
            "bob"   : { ... }
        }

    Private keys are stored in plaintext for prototype purposes.
    In production, derive an encryption key from a passphrase using
    Argon2id and encrypt each private key with AES-256-GCM before
    writing. Add a "salt" and "nonce" field per wallet entry.
    """

    def __init__(self, data_dir: str = DEFAULT_DATA_DIR):
        _ensure_dir(data_dir)
        self.filepath = os.path.join(data_dir, "wallets.json")
        self._wallets : dict[str, QuantumWallet] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.filepath):
            return
        try:
            with open(self.filepath, "r") as f:
                raw = json.load(f)
            for name, entry in raw.items():
                wallet             = QuantumWallet.__new__(QuantumWallet)
                wallet.public_key  = base64.b64decode(entry["public_key"])
                wallet.private_key = base64.b64decode(entry["private_key"])
                wallet.address     = entry["address"]
                wallet._signer     = oqs.Signature(ALGORITHM, wallet.private_key)
                self._wallets[name] = wallet
        except Exception as e:
            print(f"could not load wallets from {self.filepath}: {e}")

    def _persist(self) -> None:
        raw = {}
        for name, wallet in self._wallets.items():
            raw[name] = {
                "algorithm"   : ALGORITHM,
                "address"     : wallet.address,
                "public_key"  : base64.b64encode(wallet.public_key).decode(),
                "private_key" : base64.b64encode(wallet.private_key).decode()
            }
        _atomic_write(self.filepath, json.dumps(raw, indent=2))

    def create(self, name: str) -> QuantumWallet:
        """
        Generate a new wallet, store it under name, and persist.
        Raises ValueError if the name is already taken.
        """
        if name in self._wallets:
            raise ValueError(f"wallet '{name}' already exists")
        wallet = QuantumWallet()
        self._wallets[name] = wallet
        self._persist()
        return wallet

    def get(self, name: str) -> QuantumWallet | None:
        return self._wallets.get(name)

    def list_wallets(self) -> list[str]:
        return list(self._wallets.keys())

    def delete(self, name: str) -> None:
        if name not in self._wallets:
            raise ValueError(f"wallet '{name}' not found")
        del self._wallets[name]
        self._persist()