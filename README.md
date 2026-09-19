# Quantum Chain

A small blockchain prototype with a websocket node, in-memory peer discovery, and an aiohttp REST API.

## Quick start

1. Create and activate a Python virtual environment.
2. Install dependencies:

   python -m pip install -r requirements.txt

3. Copy the sample environment file:

   copy .env.example .env

4. Start the node:

   set WS_PORT=8123
   set PORT=9010
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
- Generated runtime files such as wallet and peer state are stored under data/ and ignored by git.
