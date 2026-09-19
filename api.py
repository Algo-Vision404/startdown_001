# api.py
#
# REST API layer for the quantum chain network.
#
# Built with aiohttp because it integrates natively with asyncio.
# Every handler is a coroutine running in the same event loop as
# the node network, so handler code can await node methods directly
# without threading or queues.
#
# All responses are JSON. Error responses always include a field
# named "error" with a plain English description.
#
# Endpoints:
#
#   GET  /status                              network summary
#   GET  /consensus                           consensus check
#   GET  /chain/valid                         validate all chains
#
#   GET  /node/{port}/status                  single node status
#   GET  /node/{port}/chain                   full chain
#   GET  /node/{port}/chain/height            chain height only
#   GET  /node/{port}/block/{index}           single block
#   GET  /node/{port}/mempool                 pending transactions
#   GET  /node/{port}/balance/{address}       address balance
#   GET  /node/{port}/utxos/{address}         UTXOs for an address
#   GET  /node/{port}/utxos/size              total UTXO count
#
#   POST /wallet/create                       create a named wallet
#   GET  /wallet/list                         all wallets
#   GET  /wallet/{name}                       single wallet info
#   GET  /wallet/{name}/balance               wallet balance
#
#   POST /tx/send                             sign and submit a transaction

import json
import logging

from aiohttp import web

from node import Node
from storage import WalletStore
from transaction import Transaction


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
    """
    Resolve a node from the {port} URL path parameter.
    Returns None if the port is missing, non-integer, or not found.
    """
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
        "index"          : block.index,
        "hash"           : block.hash,
        "previous_hash"  : block.previous_hash,
        "nonce"          : block.nonce,
        "timestamp"      : block.timestamp,
        "tx_count"       : block.transaction_count(),
        "internally_valid": block.is_internally_valid(),
        "transactions"   : [format_tx(tx) for tx in block.transactions]
    }


# ─────────────────────────────────────────────────────────────
# Network routes
# ─────────────────────────────────────────────────────────────

async def handle_network_status(request: web.Request) -> web.Response:
    """
    GET /status
    Summary of all nodes in the network.
    """
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
    """
    GET /consensus
    Check whether all nodes agree on chain height and tip hash.
    """
    nodes      = request.app["nodes"]
    heights    = {n.port: n.chain.height()         for n in nodes}
    tips       = {n.port: n.chain.chain[-1].hash   for n in nodes}
    utxo_sizes = {n.port: n.chain.utxo_set.size()  for n in nodes}

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
    """
    GET /chain/valid
    Run full chain validation on every node and return per-node results.
    """
    nodes  = request.app["nodes"]
    result = {}

    for node in nodes:
        result[node.port] = node.chain.is_valid()

    all_valid = all(result.values())
    return ok({"all_valid": all_valid, "per_node": result})


# ─────────────────────────────────────────────────────────────
# Node routes
# ─────────────────────────────────────────────────────────────

async def handle_node_status(request: web.Request) -> web.Response:
    """
    GET /node/{port}/status
    Detailed status for a single node.
    """
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    s = node.status()
    return ok({
        "port"         : s["port"],
        "height"       : s["height"],
        "chain_valid"  : s["chain_valid"],
        "mempool_size" : s["mempool"],
        "mining"       : s["mining"],
        "peers"        : s["peers"],
        "tip_hash"     : s["tip_hash"],
        "utxo_count"   : node.chain.utxo_set.size(),
        "miner_address": getattr(node, "miner_address", None)
    })


async def handle_node_chain(request: web.Request) -> web.Response:
    """
    GET /node/{port}/chain
    Full chain for a node including all blocks and transactions.

    For a long chain this response will be large.
    A production API would add ?from= and ?to= query params for
    pagination. For this prototype the full chain is returned.
    """
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
    """
    GET /node/{port}/chain/height
    Chain height only. Lightweight polling endpoint.
    """
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    return ok({
        "port"   : node.port,
        "height" : node.chain.height()
    })


async def handle_node_block(request: web.Request) -> web.Response:
    """
    GET /node/{port}/block/{index}
    A single block by index including all transactions.
    """
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


async def handle_node_mempool(request: web.Request) -> web.Response:
    """
    GET /node/{port}/mempool
    All pending transactions in a node's mempool, sorted by fee descending.
    """
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    return ok({
        "port"    : node.port,
        "pending" : len(node.mempool),
        "txs"     : [format_tx(tx) for tx in node.mempool]
    })


async def handle_node_balance(request: web.Request) -> web.Response:
    """
    GET /node/{port}/balance/{address}
    O(1) balance lookup from the UTXO set for a raw address string.
    """
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    address = request.match_info.get("address", "").strip()
    if not address:
        return err("address is required")

    balance = node.balance(address)
    return ok({
        "address"    : address,
        "balance"    : balance,
        "node"       : node.port,
        "utxo_count" : len(node.utxos_for(address))
    })


async def handle_node_utxos(request: web.Request) -> web.Response:
    """
    GET /node/{port}/utxos/{address}
    All unspent outputs owned by a given address.

    Returns each UTXO's tx_id, index, and amount so a client can
    construct raw transactions manually if needed.
    """
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    address = request.match_info.get("address", "").strip()
    if not address:
        return err("address is required")

    utxos   = node.utxos_for(address)
    balance = node.balance(address)

    return ok({
        "address" : address,
        "balance" : balance,
        "count"   : len(utxos),
        "utxos"   : utxos
    })


async def handle_utxo_set_size(request: web.Request) -> web.Response:
    """
    GET /node/{port}/utxos/size
    Total number of unspent outputs in the UTXO set.

    A useful health metric. A growing UTXO set means coins are being
    created faster than they are being consolidated. A shrinking set
    means outputs are being spent and consolidated.
    """
    node = node_from_request(request)
    if not node:
        return err("node not found", 404)

    return ok({
        "port"       : node.port,
        "utxo_count" : node.chain.utxo_set.size()
    })


# ─────────────────────────────────────────────────────────────
# Wallet routes
# ─────────────────────────────────────────────────────────────

async def handle_wallet_create(request: web.Request) -> web.Response:
    """
    POST /wallet/create
    Body: { "name": "alice" }

    Generates a new ML-DSA-65 keypair and persists the wallet.
    Returns the address and public key size.
    Never returns the private key in any response.
    """
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
    """
    GET /wallet/list
    All stored wallet names and addresses.
    """
    store   = request.app["wallet_store"]
    names   = store.list_wallets()
    wallets = []

    for name in names:
        w = store.get(name)
        wallets.append({
            "name"    : name,
            "address" : w.address
        })

    return ok({"count": len(wallets), "wallets": wallets})


async def handle_wallet_get(request: web.Request) -> web.Response:
    """
    GET /wallet/{name}
    Info for a single named wallet.
    Does not expose the private key.
    """
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
    """
    GET /wallet/{name}/balance
    Balance and UTXO count for a named wallet.
    Uses node 0 as the source of truth.
    In a consistent network all nodes have the same UTXO set.
    """
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
    Build, sign, and submit a UTXO-based transaction.

    Body:
    {
        "sender"    : "alice",
        "recipient" : "bob",
        "amount"    : 50.0,
        "fee"       : 0.01,      (optional, defaults to 0.0)
        "node_port" : 8000       (optional, defaults to first node)
    }

    Coin selection is automatic (largest UTXO first).
    Change is returned to the sender automatically.

    Note on signing model:
        The server holds private keys and signs on the client's behalf.
        This is acceptable for a prototype. In production, signing
        should happen client-side using a dedicated wallet application,
        and the client should POST the fully signed transaction bytes.
        That model requires a separate signing library and is out of
        scope here.
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
    """
    Construct and configure the aiohttp application.

    Shared state is stored in app[] so every handler can access it
    without globals. aiohttp passes the app object into every request.
    """
    app = web.Application()

    app["nodes"]        = nodes
    app["node_map"]     = {node.port: node for node in nodes}
    app["wallet_store"] = wallet_store

    # Network
    app.router.add_get ("/status",                          handle_network_status)
    app.router.add_get ("/consensus",                       handle_consensus)
    app.router.add_get ("/chain/valid",                     handle_chain_valid)

    # Node — order matters: more specific routes before less specific
    app.router.add_get ("/node/{port}/status",              handle_node_status)
    app.router.add_get ("/node/{port}/chain/height",        handle_node_height)
    app.router.add_get ("/node/{port}/chain",               handle_node_chain)
    app.router.add_get ("/node/{port}/block/{index}",       handle_node_block)
    app.router.add_get ("/node/{port}/mempool",             handle_node_mempool)
    app.router.add_get ("/node/{port}/balance/{address}",   handle_node_balance)
    app.router.add_get ("/node/{port}/utxos/size",          handle_utxo_set_size)
    app.router.add_get ("/node/{port}/utxos/{address}",     handle_node_utxos)

    # Wallet
    app.router.add_post("/wallet/create",                   handle_wallet_create)
    app.router.add_get ("/wallet/list",                     handle_wallet_list)
    app.router.add_get ("/wallet/{name}/balance",           handle_wallet_balance)
    app.router.add_get ("/wallet/{name}",                   handle_wallet_get)

    # Transaction
    app.router.add_post("/tx/send",                         handle_tx_send)

    return app


async def start_api(
    app  : web.Application,
    host : str = "localhost",
    port : int = 9000
) -> web.AppRunner:
    """
    Start the aiohttp server inside the existing asyncio event loop.

    Uses AppRunner + TCPSite instead of web.run_app() because
    web.run_app() blocks and manages its own event loop, which
    conflicts with the node network already running in ours.
    """
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logging.info(f"API server listening on http://{host}:{port}")
    return runner