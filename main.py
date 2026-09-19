# main.py
#
# Starts the four-node network, the REST API, and the interactive CLI.
# All three run concurrently in the same asyncio event loop.

import asyncio
import logging

from node import Node
from cli import CLI
from storage import WalletStore
from api import build_app, start_api


logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(message)s",
    datefmt = "%H:%M:%S"
)


PORTS = [8000, 8001, 8002, 8003]

PEERS = {
    8000 : [8001, 8002, 8003],
    8001 : [8002, 8003],
    8002 : [8003],
    8003 : []
}

API_HOST = "localhost"
API_PORT = 9000


async def main():

    # ── Nodes ─────────────────────────────────────────────────
    nodes = [Node(port, PEERS[port]) for port in PORTS]

    for node in nodes:
        await node.serve()

    await asyncio.sleep(0.5)

    for node in nodes:
        await node.connect_to_peers()

    await asyncio.sleep(0.5)

    # ── Wallet store ──────────────────────────────────────────
    wallet_store = WalletStore()

    # ── REST API ──────────────────────────────────────────────
    app     = build_app(nodes, wallet_store)
    runner  = await start_api(app, API_HOST, API_PORT)

    print(f"\nREST API  : http://{API_HOST}:{API_PORT}")
    print(f"Nodes     : {PORTS}")
    print(f"Data dir  : data/\n")

    # ── CLI ───────────────────────────────────────────────────
    cli = CLI(nodes, wallet_store)
    await cli.run()

    # ── Shutdown ──────────────────────────────────────────────
    for node in nodes:
        node.close()

    await runner.cleanup()
    print("done.")


if __name__ == "__main__":
    asyncio.run(main())