# message.py

import json
import base64

from Transaction import Transaction
from block import Block
from merkle import MerkleTree


class MessageType:
    HANDSHAKE     = "HANDSHAKE"
    TRANSACTION   = "TRANSACTION"
    BLOCK         = "BLOCK"
    REQUEST_CHAIN = "REQUEST_CHAIN"
    CHAIN         = "CHAIN"
    GET_PEERS     = "GET_PEERS"       # request peer list from a node
    PEERS         = "PEERS"           # response containing peer list
    PING          = "PING"            # liveness check
    PONG          = "PONG"            # liveness response


def serialize_transaction(tx: Transaction) -> dict:
    return {
        "sender"            : tx.sender,
        "inputs"            : tx.inputs,
        "outputs"           : tx.outputs,
        "fee"               : tx.fee,
        "is_coinbase"       : tx.is_coinbase,
        "timestamp"         : tx.timestamp,
        "tx_id"             : tx.tx_id,
        "signature"         : base64.b64encode(tx.signature).decode(),
        "sender_public_key" : base64.b64encode(tx.sender_public_key).decode()
    }


def deserialize_transaction(data: dict) -> Transaction:
    tx                   = Transaction.__new__(Transaction)
    tx.sender            = data["sender"]
    tx.inputs            = data["inputs"]
    tx.outputs           = data["outputs"]
    tx.fee               = data["fee"]
    tx.is_coinbase       = data["is_coinbase"]
    tx.timestamp         = data["timestamp"]
    tx.tx_id             = data["tx_id"]
    tx.signature         = base64.b64decode(data["signature"])
    tx.sender_public_key = base64.b64decode(data["sender_public_key"])
    return tx


def serialize_block(block: Block) -> dict:
    return {
        "index"         : block.index,
        "timestamp"     : block.timestamp,
        "previous_hash" : block.previous_hash,
        "hash"          : block.hash,
        "nonce"         : block.nonce,
        "transactions"  : [serialize_transaction(tx) for tx in block.transactions]
    }


def deserialize_block(data: dict) -> Block:
    block               = Block.__new__(Block)
    block.index         = data["index"]
    block.timestamp     = data["timestamp"]
    block.previous_hash = data["previous_hash"]
    block.hash          = data["hash"]
    block.nonce         = data["nonce"]
    block.transactions  = [deserialize_transaction(tx) for tx in data["transactions"]]
    block._merkle_tree  = MerkleTree(block.transactions)
    block.merkle_root   = block._merkle_tree.root
    return block


def build(msg_type: str, sender_port: int, data=None) -> str:
    msg = {"type": msg_type, "sender_port": sender_port}
    if data is not None:
        msg["data"] = data
    return json.dumps(msg)


def parse(raw: str) -> dict:
    return json.loads(raw)