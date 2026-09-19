# message.py
#
# Network message protocol.
#
# Every message sent between nodes is a JSON object with three fields:
#
#     type        : one of the MessageType constants below
#     sender_port : the port of the node that created the message
#     data        : payload (absent for messages that carry no payload)
#
# Transactions and blocks contain bytes fields (signatures, public keys,
# hashes). JSON cannot represent raw bytes, so all bytes fields are
# base64-encoded before transmission and decoded on arrival.
#
# Message types:
#
#     HANDSHAKE       : sent immediately after a connection is established
#                       so the receiver knows which port the sender is on.
#
#     TRANSACTION     : a signed transaction being propagated across the
#                       network. Any node that receives one it has not seen
#                       before validates it, adds it to its mempool, and
#                       forwards it to all peers.
#
#     BLOCK           : a mined block being propagated. Any node that
#                       receives one validates it and, if valid, appends it
#                       to its chain and forwards it.
#
#     REQUEST_CHAIN   : sent by a node that discovers it is behind. Peers
#                       respond with their full chain.
#
#     CHAIN           : the full chain, sent in response to REQUEST_CHAIN.
#                       The receiver adopts it if it is longer and valid.

import json
import base64

from Transaction import Transaction
from block import Block


class MessageType:
    HANDSHAKE      = "HANDSHAKE"
    TRANSACTION    = "TRANSACTION"
    BLOCK          = "BLOCK"
    REQUEST_CHAIN  = "REQUEST_CHAIN"
    CHAIN          = "CHAIN"


# ─────────────────────────────────────────────────────────────
# Transaction serialization
# ─────────────────────────────────────────────────────────────

def serialize_transaction(tx: Transaction) -> dict:
    """
    Convert a Transaction to a JSON-safe dict.

    Bytes fields (signature, sender_public_key) are base64-encoded.
    All core fields are included so the receiver can independently
    verify the signature without trusting any other field.
    """
    return {
        "sender"            : tx.sender,
        "recipient"         : tx.recipient,
        "amount"            : tx.amount,
        "timestamp"         : tx.timestamp,
        "tx_id"             : tx.tx_id,
        "signature"         : base64.b64encode(tx.signature).decode(),
        "sender_public_key" : base64.b64encode(tx.sender_public_key).decode()
    }


def deserialize_transaction(data: dict) -> Transaction:
    """
    Reconstruct a Transaction from a network dict.

    Uses __new__ to bypass __init__ since we are restoring a
    transaction that was already created and signed elsewhere.
    We must not regenerate any fields — every field must be
    restored exactly as it was when the sender signed it.
    """
    tx                   = Transaction.__new__(Transaction)
    tx.sender            = data["sender"]
    tx.recipient         = data["recipient"]
    tx.amount            = data["amount"]
    tx.timestamp         = data["timestamp"]
    tx.tx_id             = data["tx_id"]
    tx.signature         = base64.b64decode(data["signature"])
    tx.sender_public_key = base64.b64decode(data["sender_public_key"])
    return tx


# ─────────────────────────────────────────────────────────────
# Block serialization
# ─────────────────────────────────────────────────────────────

def serialize_block(block: Block) -> dict:
    """
    Convert a Block to a JSON-safe dict.

    Transactions are serialized with full signature data so the
    receiver can verify every signature independently.
    """
    return {
        "index"         : block.index,
        "timestamp"     : block.timestamp,
        "previous_hash" : block.previous_hash,
        "hash"          : block.hash,
        "nonce"         : block.nonce,
        "transactions"  : [serialize_transaction(tx) for tx in block.transactions]
    }


def deserialize_block(data: dict) -> Block:
    """
    Reconstruct a Block from a network dict.

    Uses __new__ to bypass __init__ so the original timestamp,
    nonce, and hash are preserved exactly. If we called __init__,
    it would generate a new timestamp and a new hash.
    """
    block               = Block.__new__(Block)
    block.index         = data["index"]
    block.timestamp     = data["timestamp"]
    block.previous_hash = data["previous_hash"]
    block.hash          = data["hash"]
    block.nonce         = data["nonce"]
    block.transactions  = [deserialize_transaction(tx) for tx in data["transactions"]]
    return block


# ─────────────────────────────────────────────────────────────
# Message construction and parsing
# ─────────────────────────────────────────────────────────────

def build(msg_type: str, sender_port: int, data=None) -> str:
    """Serialize a message to a JSON string for transmission."""
    msg = {
        "type"        : msg_type,
        "sender_port" : sender_port
    }
    if data is not None:
        msg["data"] = data
    return json.dumps(msg)


def parse(raw: str) -> dict:
    """Deserialize a received JSON string into a message dict."""
    return json.loads(raw)