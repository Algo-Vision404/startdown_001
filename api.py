# api.py

import json
import logging
import asyncio

from aiohttp import web

from node import Node
from storage import WalletStore
from Transaction import Transaction
from message import build, serialize_block, MessageType


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def ok(data: dict | list) -> web.Response:
    return web.Response(
        text         = json.dumps(data, indent=2),
        content_type = "application/json",
        status       = 200
    )


def err(message: str, status: int = 400) -> web.Response:
    return web.Response(
        text         = json.dumps({"error": message}),
        content_type = "application/json",
        status       = status
    )


def node_from_request(request: web.Request) -> Node | None:
    try:
        port = int(request.match_info["port"])
    except (KeyError, ValueError):
        return None
    return request.app["node_map"].get(port)


def format_tx(tx) -> dict:
    return {
        "tx_id"      : tx.tx_id,
        "sender"     : tx.sender,
        "inputs"     : tx.inputs,
        "outputs"    : tx.outputs,
        "fee"        : tx.fee,
        "is_coinbase": tx.is_coinbase,
        "timestamp"  : tx.timestamp,
        "sig_bytes"  : len(tx.signature),
        "valid"      : tx.is_valid()
    }


def format_block(block) -> dict:
    return {
        "index"            : block.index,
        "hash"             : block.hash,
        "previous_hash"    : block.previous_hash,
        "nonce"            : block.nonce,
        "timestamp"        : block.timestamp,
        "tx_count"         : block.transaction_count(),
        "internally_valid" : block.is_internally_valid(),
        "transactions"     : [format_tx(tx) for tx in block.transactions]
    }


# ─────────────────────────────────────────────────────────────
# Network routes
# ─────────────────────────────────────────────────────────────

async def handle_network_status(request: web.Request) -> web.Response:
    """GET /status"""
    nodes  = request.app["nodes"]
    result = []

    for node in nodes:
        s = node.status()
        result.append({
            "port"        : s["port"],
            "height"      : s["height"],
            "chain_valid" : s["chain_valid"],
            "mempool"     : s["mempool"],
            "mining"      : s["mining"],
            "peers"       : s["peers"],
            "tip_hash"    : s["tip_hash"],
            "utxo_count"  : node.chain.utxo_set.size()
        })

    return ok({"nodes": result, "count": len(nodes)})


async def handle_consensus(request: web.Request) -> web.Response:
    """GET /consensus"""
    nodes      = request.app["nodes"]
    heights    = {n.port: n.chain.height()        for n in nodes}
    tips       = {n.port: n.chain.chain[-1].hash  for n in nodes}
    utxo_sizes = {n.port: n.chain.utxo_set.size() for n in nodes}

    unique_h   = set(heights.values())
    unique_t   = set(tips.values())
    reached    = len(unique_h) == 1 and len(unique_t) == 1

    return ok({
        "consensus_reached" : reached,
        "heights"           : heights,
        "tip_hashes"        : tips,
        "utxo_counts"       : utxo_sizes,
        "agreed_height"     : list(unique_h)[0] if reached else None,
        "agreed_tip"        : list(unique_t)[0] if reached else None
    })


async def handle_chain_valid(request: web.Request) -> web.Response:
    """GET /chain/valid"""
    nodes  = request.app["nodes"]
    result = {node.port: node.chain.is_valid() for node in nodes}
    return ok({"all_valid": all(result.values()), "per_node": result})


# ─────────────────────────────────────────────────────────────
# Node routes
# ─────────────────────────────────────────────────────────────

async def handle_node_status(request: web.Request) -> web.Response:
    """GET /node/{port}/status"""
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    s = node.status()
    return ok({
        "port"          : s["port"],
        "height"        : s["height"],
        "chain_valid"   : s["chain_valid"],
        "mempool_size"  : s["mempool"],
        "mining"        : s["mining"],
        "peers"         : s["peers"],
        "tip_hash"      : s["tip_hash"],
        "utxo_count"    : node.chain.utxo_set.size(),
        "miner_address" : getattr(node, "miner_address", None)
    })


async def handle_peers(request: web.Request) -> web.Response:
    """GET /node/{port}/peers"""
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    peers = [peer.to_dict() for peer in node.peer_mgr.all_peers()]
    return ok({
        "port": node.port,
        "summary": node.peer_mgr.summary(),
        "peers": peers,
    })


async def handle_add_peer(request: web.Request) -> web.Response:
    """POST /node/{port}/peers/add"""
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    try:
        body = await request.json()
    except Exception:
        return err("request body must be valid JSON")

    host = body.get("host", "").strip()
    port_raw = body.get("port")

    if not host:
        return err("'host' is required")
    if port_raw is None:
        return err("'port' is required")

    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        return err("'port' must be an integer")

    added = node.peer_mgr.add(host, port)

    if node.peer_mgr.needs_peers():
        asyncio.create_task(node._connect_to(host, port))

    return ok({
        "added": added,
        "host": host,
        "port": port,
        "summary": node.peer_mgr.summary(),
    })


async def handle_node_chain(request: web.Request) -> web.Response:
    """GET /node/{port}/chain"""
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    return ok({
        "port"   : node.port,
        "height" : node.chain.height(),
        "valid"  : node.chain.is_valid(),
        "chain"  : [format_block(b) for b in node.chain.chain]
    })


async def handle_node_height(request: web.Request) -> web.Response:
    """GET /node/{port}/chain/height"""
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    return ok({"port": node.port, "height": node.chain.height()})


async def handle_node_block(request: web.Request) -> web.Response:
    """GET /node/{port}/block/{index}"""
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    try:
        index = int(request.match_info["index"])
    except ValueError:
        return err("block index must be an integer")

    if index < 0 or index >= node.chain.height():
        return err(
            f"block {index} does not exist. "
            f"chain height is {node.chain.height()}",
            404
        )

    return ok(format_block(node.chain.chain[index]))


async def handle_merkle_root(request: web.Request) -> web.Response:
    """GET /node/{port}/block/{index}/merkle"""
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    try:
        index = int(request.match_info["index"])
    except ValueError:
        return err("block index must be an integer")

    if index < 0 or index >= node.chain.height():
        return err(f"block {index} does not exist", 404)

    block = node.chain.chain[index]
    return ok({
        "block_index": block.index,
        "block_hash": block.hash,
        "merkle_root": block.merkle_root,
        "tx_count": block.transaction_count(),
        "tree": block._merkle_tree.to_dict(),
    })


async def handle_merkle_proof(request: web.Request) -> web.Response:
    """GET /node/{port}/block/{index}/proof/{tx_index}"""
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    try:
        block_index = int(request.match_info["index"])
        tx_index = int(request.match_info["tx_index"])
    except ValueError:
        return err("block index and tx index must be integers")

    if block_index < 0 or block_index >= node.chain.height():
        return err(f"block {block_index} does not exist", 404)

    block = node.chain.chain[block_index]
    if tx_index < 0 or tx_index >= block.transaction_count():
        return err(
            f"tx index {tx_index} out of range. "
            f"block has {block.transaction_count()} transactions",
            404,
        )

    proof_data = block.merkle_proof(tx_index)
    if proof_data is None:
        return err(
            f"tx index {tx_index} out of range. "
            f"block has {block.transaction_count()} transactions",
            404,
        )

    return ok(proof_data)


async def handle_verify_proof(request: web.Request) -> web.Response:
    """POST /merkle/verify"""
    try:
        body = await request.json()
    except Exception:
        return err("request body must be valid JSON")

    tx_hash = body.get("tx_hash", "").strip()
    proof = body.get("proof")
    root = body.get("merkle_root", "").strip()

    if not tx_hash:
        return err("'tx_hash' is required")
    if proof is None:
        return err("'proof' is required")
    if not root:
        return err("'merkle_root' is required")
    if not isinstance(proof, list):
        return err("'proof' must be a list")

    from merkle import MerkleTree

    try:
        valid = MerkleTree.verify_proof(tx_hash, proof, root)
    except Exception as error:
        return err(f"proof verification failed: {error}")

    return ok({
        "valid": valid,
        "tx_hash": tx_hash,
        "merkle_root": root,
        "proof_steps": len(proof),
    })


async def handle_block_header(request: web.Request) -> web.Response:
    """GET /node/{port}/block/{index}/header"""
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    try:
        index = int(request.match_info["index"])
    except ValueError:
        return err("block index must be an integer")

    if index < 0 or index >= node.chain.height():
        return err(f"block {index} does not exist", 404)

    return ok(node.chain.chain[index].header())


async def handle_node_mempool(request: web.Request) -> web.Response:
    """GET /node/{port}/mempool"""
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    return ok({
        "port"    : node.port,
        "pending" : len(node.mempool),
        "txs"     : [format_tx(tx) for tx in node.mempool]
    })


async def handle_node_balance(request: web.Request) -> web.Response:
    """GET /node/{port}/balance/{address}"""
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    address = request.match_info.get("address", "").strip()
    if not address:
        return err("address is required")

    return ok({
        "address"    : address,
        "balance"    : node.balance(address),
        "node"       : node.port,
        "utxo_count" : len(node.utxos_for(address))
    })


async def handle_node_utxos(request: web.Request) -> web.Response:
    """GET /node/{port}/utxos/{address}"""
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    address = request.match_info.get("address", "").strip()
    if not address:
        return err("address is required")

    utxos = node.utxos_for(address)
    return ok({
        "address" : address,
        "balance" : node.balance(address),
        "count"   : len(utxos),
        "utxos"   : utxos
    })


async def handle_utxo_set_size(request: web.Request) -> web.Response:
    """GET /node/{port}/utxos/size"""
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    return ok({
        "port"       : node.port,
        "utxo_count" : node.chain.utxo_set.size()
    })


async def handle_force_mine(request: web.Request) -> web.Response:
    """
    POST /node/{port}/mine

    Force a node to mine a block immediately regardless of mempool size.
    This is the bootstrap mechanism. The first call creates the first
    coinbase UTXO, giving the miner wallet its first spendable coins.
    """
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    if node.mining:
        return err("node is already mining", 409)

    miner_address = getattr(node, "miner_address", "MINER_UNSET")
    if miner_address == "MINER_UNSET":
        return err("node has no miner address configured", 500)

    node.mining = True
    to_mine = list(node.mempool[:node.BLOCK_SIZE])

    try:
        loop  = asyncio.get_event_loop()
        block = await loop.run_in_executor(
            None,
            node.chain.mine_block,
            to_mine,
            miner_address
        )

        if block.previous_hash != node.chain.chain[-1].hash:
            return err("chain tip moved during mining, try again")

        node.chain.append_block(block)
        node.seen_block_hashes.add(block.hash)
        node._save_chain()

        mined_ids    = {tx.tx_id for tx in to_mine}
        node.mempool = [
            tx for tx in node.mempool
            if tx.tx_id not in mined_ids
        ]
        node._recompute_pending_inputs()

        await node._broadcast(
            build(MessageType.BLOCK, node.port, serialize_block(block))
        )

        coinbase = block.transactions[0] if block.transactions else None
        reward   = coinbase.total_output() if coinbase else 0

        return ok({
            "mined"           : True,
            "block_index"     : block.index,
            "hash"            : block.hash,
            "nonce"           : block.nonce,
            "height"          : node.chain.height(),
            "tx_count"        : block.transaction_count(),
            "coinbase_reward" : reward,
            "miner_address"   : miner_address,
            "utxo_count"      : node.chain.utxo_set.size()
        })

    except Exception as e:
        return err(f"mining failed: {e}")
    finally:
        node.mining = False


# ─────────────────────────────────────────────────────────────
# Wallet routes
# ─────────────────────────────────────────────────────────────

async def handle_wallet_create(request: web.Request) -> web.Response:
    """POST /wallet/create"""
    try:
        body = await request.json()
    except Exception:
        return err("request body must be valid JSON")

    name = body.get("name", "").strip()
    if not name:
        return err("'name' field is required")

    store = request.app["wallet_store"]

    try:
        wallet = store.create(name)
    except ValueError as e:
        return err(str(e))

    return ok({
        "name"      : name,
        "address"   : wallet.address,
        "algorithm" : "ML-DSA-65",
        "pubkey_sz" : len(wallet.public_key)
    })


async def handle_wallet_list(request: web.Request) -> web.Response:
    """GET /wallet/list"""
    store   = request.app["wallet_store"]
    names   = store.list_wallets()
    wallets = []

    for name in names:
        w = store.get(name)
        wallets.append({"name": name, "address": w.address})

    return ok({"count": len(wallets), "wallets": wallets})


async def handle_wallet_get(request: web.Request) -> web.Response:
    """GET /wallet/{name}"""
    store  = request.app["wallet_store"]
    name   = request.match_info.get("name", "")
    wallet = store.get(name)

    if not wallet:
        return err(f"wallet '{name}' not found", 404)

    return ok({
        "name"      : name,
        "address"   : wallet.address,
        "algorithm" : "ML-DSA-65",
        "pubkey_sz" : len(wallet.public_key)
    })


async def handle_wallet_balance(request: web.Request) -> web.Response:
    """GET /wallet/{name}/balance"""
    store  = request.app["wallet_store"]
    name   = request.match_info.get("name", "")
    wallet = store.get(name)

    if not wallet:
        return err(f"wallet '{name}' not found", 404)

    node    = request.app["nodes"][0]
    balance = node.balance(wallet.address)
    utxos   = node.utxos_for(wallet.address)

    return ok({
        "name"       : name,
        "address"    : wallet.address,
        "balance"    : balance,
        "utxo_count" : len(utxos)
    })


# ─────────────────────────────────────────────────────────────
# Transaction routes
# ─────────────────────────────────────────────────────────────

async def handle_tx_send(request: web.Request) -> web.Response:
    """
    POST /tx/send
    Body:
    {
        "sender"    : "alice",
        "recipient" : "bob",
        "amount"    : 50.0,
        "fee"       : 0.01,
        "node_port" : 8000
    }
    fee and node_port are optional.
    """
    try:
        body = await request.json()
    except Exception:
        return err("request body must be valid JSON")

    sender_name    = body.get("sender", "").strip()
    recipient_name = body.get("recipient", "").strip()
    amount_raw     = body.get("amount")
    fee_raw        = body.get("fee", 0.0)
    node_port      = body.get("node_port")

    if not sender_name:
        return err("'sender' is required")
    if not recipient_name:
        return err("'recipient' is required")
    if amount_raw is None:
        return err("'amount' is required")

    try:
        amount = float(amount_raw)
    except (TypeError, ValueError):
        return err("'amount' must be a number")

    try:
        fee = float(fee_raw)
    except (TypeError, ValueError):
        return err("'fee' must be a number")

    if amount <= 0:
        return err("'amount' must be positive")
    if fee < 0:
        return err("'fee' cannot be negative")

    store    = request.app["wallet_store"]
    node_map = request.app["node_map"]
    nodes    = request.app["nodes"]

    sender_wallet    = store.get(sender_name)
    recipient_wallet = store.get(recipient_name)

    if not sender_wallet:
        return err(f"sender wallet '{sender_name}' not found")
    if not recipient_wallet:
        return err(f"recipient wallet '{recipient_name}' not found")

    if node_port is not None:
        try:
            node = node_map.get(int(node_port))
        except (TypeError, ValueError):
            return err("'node_port' must be an integer")
        if not node:
            return err(
                f"no node on port {node_port}. "
                f"available: {list(node_map.keys())}"
            )
    else:
        node = nodes[0]

    try:
        tx = Transaction.transfer(
            sender_wallet     = sender_wallet,
            utxo_set          = node.chain.utxo_set,
            recipient_address = recipient_wallet.address,
            amount            = amount,
            fee               = fee
        )
    except Exception as e:
        return err(str(e))

    await node.submit_transaction(tx)

    return ok({
        "submitted"   : True,
        "tx_id"       : tx.tx_id,
        "sender"      : sender_name,
        "recipient"   : recipient_name,
        "amount"      : amount,
        "fee"         : fee,
        "inputs_used" : len(tx.inputs),
        "outputs"     : tx.outputs,
        "node"        : node.port,
        "sig_bytes"   : len(tx.signature),
        "valid"       : tx.is_valid()
    })


# ─────────────────────────────────────────────────────────────
# Application factory
# ─────────────────────────────────────────────────────────────

def build_app(nodes: list, wallet_store: WalletStore) -> web.Application:
    app = web.Application()

    app["nodes"]        = nodes
    app["node_map"]     = {node.port: node for node in nodes}
    app["wallet_store"] = wallet_store

    # Network
    app.router.add_get ("/status",                         handle_network_status)
    app.router.add_get ("/consensus",                      handle_consensus)
    app.router.add_get ("/chain/valid",                    handle_chain_valid)

    # Node — more specific routes must come before less specific ones
    app.router.add_get ("/node/{port}/status",             handle_node_status)
    app.router.add_get ("/node/{port}/peers",              handle_peers)
    app.router.add_post("/node/{port}/peers/add",           handle_add_peer)
    app.router.add_get ("/node/{port}/chain/height",       handle_node_height)
    app.router.add_get ("/node/{port}/chain",              handle_node_chain)
    app.router.add_get ("/node/{port}/block/{index}",      handle_node_block)
    app.router.add_get ("/node/{port}/block/{index}/header", handle_block_header)
    app.router.add_get ("/node/{port}/block/{index}/merkle", handle_merkle_root)
    app.router.add_get ("/node/{port}/block/{index}/proof/{tx_index}", handle_merkle_proof)
    app.router.add_get ("/node/{port}/mempool",            handle_node_mempool)
    app.router.add_get ("/node/{port}/balance/{address}",  handle_node_balance)
    app.router.add_get ("/node/{port}/utxos/size",         handle_utxo_set_size)
    app.router.add_get ("/node/{port}/utxos/{address}",    handle_node_utxos)
    app.router.add_post("/node/{port}/mine",               handle_force_mine)

    # Wallet — more specific routes before less specific ones
    app.router.add_post("/wallet/create",                  handle_wallet_create)
    app.router.add_get ("/wallet/list",                    handle_wallet_list)
    app.router.add_get ("/wallet/{name}/balance",          handle_wallet_balance)
    app.router.add_get ("/wallet/{name}",                  handle_wallet_get)

    # Transaction
    app.router.add_post("/tx/send",                        handle_tx_send)
    app.router.add_post("/merkle/verify",                   handle_verify_proof)

    return app


async def start_api(
    app  : web.Application,
    host : str = "localhost",
    port : int = 9000
) -> web.AppRunner:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logging.info(f"API server listening on http://{host}:{port}")
    return runner