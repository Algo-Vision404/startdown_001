# Quantum Chain

A small blockchain prototype with a websocket node, in-memory peer discovery, and an aiohttp REST API.

## Quick start

1. Create and activate a Python virtual environment.
2. Install dependencies:

   python -m pip install -r requirements.txt

3. Set the ports in PowerShell (the application reads process environment variables directly):

   $env:WS_PORT = "8123"
   $env:PORT = "9010"

4. Start the node:

   python main.py

5. Check the API:

   http://127.0.0.1:9010/status

## Environment variables

- API_HOST: REST API bind host
- API_PORT: REST API port. Falls back to PORT if API_PORT is not set.
- WS_HOST: websocket bind host
- WS_PORT: websocket port
- BOOTSTRAP_HOST: optional peer host to connect to at startup
- BOOTSTRAP_PORT: bootstrap peer port
- NODE_NAME: node label used by the miner wallet name

## Notes

- The project is designed to run as a single bootstrap node locally.
- Use a free websocket port on Windows if 8000 is already in use.
- Generated runtime files such as wallet, chain, UTXO, and peer state are stored under data/ and ignored by git.
- A fresh checkout creates a new miner wallet on first startup; keep data/wallets.json private because it contains wallet keys.
- `.env.example` documents the available variables, but `.env` files are not loaded automatically; set variables in the shell or configure them in your process manager.
