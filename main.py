# main.py
#
# Node 8000 is the bootstrap node.
# All other nodes only know about 8000 at startup.
# They discover 8001, 8002, 8003 through peer exchange.

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


BOOTSTRAP = [{"host": "localhost", "port": 8000}]

PORTS = [8000, 8001, 8002, 8003]

API_HOST = "localhost"
API_PORT = 9000


async def main():

    # All nodes point only to the bootstrap node (8000).
    # 8000 itself has no bootstrap — it is the entry point.
    nodes = []
    for port in PORTS:
        bootstrap = BOOTSTRAP if port != 8000 else []
        node = Node(
            host      = "localhost",
            port      = port,
            data_dir  = "data",
            bootstrap = bootstrap
        )
        nodes.append(node)

    # Start all servers first so they are ready to accept connections
    for node in nodes:
        await node.serve()

    await asyncio.sleep(0.5)

    # Assign miner wallets
    wallet_store = WalletStore()

    for node in nodes:
        miner_name = f"miner_{node.port}"
        wallet     = wallet_store.get(miner_name)
        if wallet is None:
            wallet = wallet_store.create(miner_name)
            logging.info(
                f"created miner wallet for node {node.port}"
            )
        node.miner_address = wallet.address

    # Start background tasks (peer discovery, pings, saves)
    for node in nodes:
        await node.start_background_tasks()

    # Give peer discovery time to run its first pass
    await asyncio.sleep(2.0)

    # Start REST API
    app    = build_app(nodes, wallet_store)
    runner = await start_api(app, API_HOST, API_PORT)

    print(f"\nREST API   : http://{API_HOST}:{API_PORT}")
    print(f"Bootstrap  : localhost:8000")
    print(f"Nodes      : {PORTS}")
    print(f"Data dir   : data/\n")

    cli = CLI(nodes, wallet_store)
    await cli.run()

    for node in nodes:
        node.close()

    await runner.cleanup()
    print("done.")


if __name__ == "__main__":
    asyncio.run(main())