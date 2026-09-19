# main.py
#
# Starts the four-node network and launches the interactive CLI.
# The network runs in the background while you issue commands.

import asyncio
import logging

from node import Node
from cli import CLI
from storage import WalletStore


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

    # ── CLI ───────────────────────────────────────────────────
    cli = CLI(nodes, wallet_store)
    await cli.run()

    # ── Shutdown ──────────────────────────────────────────────
    for node in nodes:
        node.close()

    print("done.")


if __name__ == "__main__":
    asyncio.run(main())