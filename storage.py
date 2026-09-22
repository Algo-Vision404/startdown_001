# storage.py — updated to persist UTXO set alongside the chain

import json
import os
import base64
import tempfile

from chain import Blockchain
from block import Block
from wallet import QuantumWallet, ALGORITHM
from Transaction import Transaction
from utxo import UTXOSet, UTXO
from message import serialize_block, deserialize_block

DEFAULT_DATA_DIR = "data"


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _atomic_write(filepath: str, content: str) -> None:
    dirpath = os.path.dirname(filepath) or "."
    fd, tmp = tempfile.mkstemp(dir=dirpath, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, filepath)
    except Exception:
        os.unlink(tmp)
        raise


class ChainStore:

    def __init__(self, port: int, data_dir: str = DEFAULT_DATA_DIR):
        _ensure_dir(data_dir)
        self.chain_path = os.path.join(data_dir, f"chain_{port}.json")
        self.utxo_path  = os.path.join(data_dir, f"utxo_{port}.json")

    def save(self, chain: Blockchain) -> None:
        """Save chain and UTXO set atomically."""
        chain_data = [serialize_block(b) for b in chain.chain]
        _atomic_write(self.chain_path, json.dumps(chain_data, indent=2))

        utxo_data = chain.utxo_set.to_dict()
        _atomic_write(self.utxo_path, json.dumps(utxo_data, indent=2))

    def load(self) -> Blockchain | None:
        if not os.path.exists(self.chain_path):
            return None

        try:
            with open(self.chain_path, "r") as f:
                chain_data = json.load(f)

            candidate          = Blockchain.__new__(Blockchain)
            candidate.chain    = [deserialize_block(b) for b in chain_data]

            replayed_utxo = UTXOSet()
            for block in candidate.chain:
                replayed_utxo.apply_block(block)
            candidate.utxo_set = replayed_utxo

            if not candidate.is_valid():
                print(f"stored chain failed validation, starting fresh")
                return None

            # The persisted UTXO snapshot is only a cache; the chain
            # itself, now fully validated above, is the source of
            # truth the snapshot is always a deterministic function
            # of. save() below writes the chain file and the UTXO
            # file as two separate atomic writes -- not one atomic
            # unit together -- so a crash between them (during a
            # reorg, say) leaves a stale snapshot next to an
            # otherwise perfectly good chain. Since a mismatch here
            # can only ever mean the snapshot is stale (never that
            # the already-validated chain is untrustworthy), repair
            # it from the chain rather than discarding a valid
            # chain's entire history over a cache file.
            needs_repair = True
            if os.path.exists(self.utxo_path):
                try:
                    with open(self.utxo_path, "r") as f:
                        utxo_data = json.load(f)
                    persisted_utxo = UTXOSet.from_dict(utxo_data)
                    persisted_keys = {
                        (entry["tx_id"], entry["index"]):
                        (entry["address"], entry["amount"])
                        for entry in persisted_utxo.to_dict()
                    }
                    replayed_keys = {
                        (entry["tx_id"], entry["index"]):
                        (entry["address"], entry["amount"])
                        for entry in replayed_utxo.to_dict()
                    }
                    needs_repair = persisted_keys != replayed_keys
                except Exception:
                    needs_repair = True  # snapshot unreadable/corrupt

            if needs_repair:
                print(
                    "stored UTXO snapshot was stale, missing, or "
                    "unreadable; repairing it from the validated chain"
                )
                try:
                    _atomic_write(
                        self.utxo_path,
                        json.dumps(replayed_utxo.to_dict(), indent=2)
                    )
                except Exception as e:
                    print(f"could not repair UTXO snapshot: {e}")

            return candidate

        except Exception as e:
            print(f"could not load chain: {e}, starting fresh")
            return None

    def exists(self) -> bool:
        return os.path.exists(self.chain_path)


class WalletStore:

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
                if entry.get("algorithm") != ALGORITHM:
                    raise ValueError(
                        f"wallet '{name}' uses an unsupported algorithm"
                    )

                wallet             = QuantumWallet.__new__(QuantumWallet)
                wallet.public_key  = base64.b64decode(entry["public_key"])
                wallet.private_key = base64.b64decode(entry["private_key"])
                wallet.address     = entry["address"]
                if wallet.address != QuantumWallet._derive_address(wallet.public_key):
                    raise ValueError(
                        f"wallet '{name}' has an invalid address"
                    )
                self._wallets[name] = wallet
        except Exception as e:
            print(f"could not load wallets: {e}")

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