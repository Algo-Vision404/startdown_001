# main.py
# Render deployment entry point: one node, no CLI, long-running process.

import asyncio
import logging
import os

from node import Node
from storage import WalletStore
from api import build_app, start_api


logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(message)s",
    datefmt = "%H:%M:%S"
)


API_PORT = int(os.environ.get("PORT", 9000))
API_HOST = "0.0.0.0"
WS_PORT = 8000
WS_HOST = "0.0.0.0"
BOOTSTRAP_HOST = os.environ.get("BOOTSTRAP_HOST", "")
BOOTSTRAP_PORT = int(os.environ.get("BOOTSTRAP_PORT", 8000))
NODE_NAME = os.environ.get("NODE_NAME", "node")


async def main():
    bootstrap = []
    if BOOTSTRAP_HOST:
        bootstrap = [{"host": BOOTSTRAP_HOST, "port": BOOTSTRAP_PORT}]
        logging.info(f"bootstrap peer: {BOOTSTRAP_HOST}:{BOOTSTRAP_PORT}")
    else:
        logging.info("no bootstrap configured - this is the bootstrap node")

    node = Node(
        host=WS_HOST,
        port=WS_PORT,
        data_dir="data",
        bootstrap=bootstrap,
    )
    await node.serve()

    wallet_store = WalletStore()
    miner_name = f"miner_{NODE_NAME}"
    wallet = wallet_store.get(miner_name)
    if wallet is None:
        wallet = wallet_store.create(miner_name)
        logging.info(f"created miner wallet: {wallet.address[:32]}...")
    else:
        logging.info(f"loaded miner wallet: {wallet.address[:32]}...")
    node.miner_address = wallet.address

    await node.start_background_tasks()

    app = build_app([node], wallet_store)
    runner = await start_api(app, API_HOST, API_PORT)

    logging.info(f"{NODE_NAME} fully started")
    logging.info(f"  REST API  : http://0.0.0.0:{API_PORT}")
    logging.info(f"  WebSocket : ws://0.0.0.0:{WS_PORT}")
    logging.info(f"  Bootstrap : {bootstrap or 'none'}")

    try:
        await asyncio.Future()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        logging.info("shutting down...")
        node.close()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())