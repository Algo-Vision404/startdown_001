# main.py

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

    nodes = [Node(port, PEERS[port]) for port in PORTS]

    for node in nodes:
        await node.serve()

    await asyncio.sleep(0.5)

    for node in nodes:
        await node.connect_to_peers()

    await asyncio.sleep(0.5)

    wallet_store = WalletStore()

    # Assign miner wallets.
    # Each node mines rewards into its own wallet.
    # Create these wallets on first run; reuse on subsequent runs.
    for i, node in enumerate(nodes):
        miner_name = f"miner_{node.port}"
        wallet     = wallet_store.get(miner_name)
        if wallet is None:
            wallet = wallet_store.create(miner_name)
            logging.info(
                f"created miner wallet for node {node.port}: "
                f"{wallet.address[:32]}..."
            )
        node.miner_address = wallet.address

    app    = build_app(nodes, wallet_store)
    runner = await start_api(app, API_HOST, API_PORT)

    print(f"\nREST API  : http://{API_HOST}:{API_PORT}")
    print(f"Nodes     : {PORTS}")
    print(f"Data dir  : data/\n")

    cli = CLI(nodes, wallet_store)
    await cli.run()

    for node in nodes:
        node.close()

    await runner.cleanup()
    print("done.")


if __name__ == "__main__":
    asyncio.run(main())