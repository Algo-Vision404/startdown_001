# Quantum Chain

Quantum Chain is a Python blockchain prototype with:

- UTXO-based transactions and block validation
- signed wallet transactions
- proof-of-work mining
- WebSocket peer communication and peer discovery
- an `aiohttp` REST API
- JSON persistence for chains, UTXOs, wallets, and peers

This repository is for learning and local development. It is not a production cryptocurrency or a secure wallet application.

## Requirements

- Python 3.11 or newer
- PowerShell on Windows, or a shell that can set environment variables
- Build tools may be required by `liboqs-python` on platforms without a compatible wheel

Install the dependencies:

```powershell
python -m pip install -r requirements.txt
```

The application imports the `oqs` module from `liboqs-python`. If `oqs` is unavailable but `cryptography` is installed, local development uses an Ed25519 compatibility fallback. The fallback is not ML-DSA and must not be treated as post-quantum security.

## Run Locally

The default entry point starts one bootstrap node. In Windows PowerShell:

```powershell
$env:WS_PORT = "8123"
$env:PORT = "9010"
python main.py
```

The services are then available at:

- REST API: `http://127.0.0.1:9010`
- WebSocket node: `ws://127.0.0.1:8123`

Check that the node is ready:

```powershell
Invoke-RestMethod http://127.0.0.1:9010/status
```

The process is intentionally long-running. Stop it with `Ctrl+C`.

### Port behavior

- `API_PORT` controls the REST API.
- `PORT` is accepted as a deployment-friendly fallback for `API_PORT`.
- `WS_PORT` controls the WebSocket node.
- If a requested port is occupied, the application searches for the next available port.
- Port checks use `0.0.0.0`, matching the server bind behavior on Windows.

## Configuration

Environment variables are read directly from the process. `.env.example` documents the available variables, but this application does not load `.env` files automatically.

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_HOST` | `0.0.0.0` | REST API bind address |
| `API_PORT` | `9000` | REST API port |
| `PORT` | unset | Fallback REST API port when `API_PORT` is unset |
| `WS_HOST` | `0.0.0.0` | WebSocket bind address |
| `WS_PORT` | `8000` | WebSocket port |
| `BOOTSTRAP_HOST` | empty | Optional peer host to contact at startup |
| `BOOTSTRAP_PORT` | `8000` | Optional bootstrap peer port |
| `NODE_NAME` | `node` | Name used for the node's miner wallet |

For a second node, use different ports and point it at the first node:

```powershell
$env:NODE_NAME = "node2"
$env:WS_PORT = "8124"
$env:PORT = "9011"
$env:BOOTSTRAP_HOST = "127.0.0.1"
$env:BOOTSTRAP_PORT = "8123"
python main.py
```

## First Blockchain Workflow

The node starts with a genesis block but no spendable coins. Mine once to create the first coinbase UTXO for the miner wallet.

```powershell
# Create a recipient wallet
$body = @{ name = "alice" } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:9010/wallet/create -Method Post `
  -ContentType "application/json" -Body $body

# Mine the first reward
$body = @{ node_port = 8123 } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:9010/node/8123/mine -Method Post `
  -ContentType "application/json" -Body $body

# Submit a transfer from the node's miner wallet
$body = @{
  sender = "miner_node"
  recipient = "alice"
  amount = 10
  fee = 0.5
  node_port = 8123
} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:9010/tx/send -Method Post `
  -ContentType "application/json" -Body $body

# Confirm the pending transaction by mining again
Invoke-RestMethod http://127.0.0.1:9010/node/8123/mine -Method Post `
  -ContentType "application/json" -Body (@{ node_port = 8123 } | ConvertTo-Json)

# Read the confirmed balance
Invoke-RestMethod http://127.0.0.1:9010/wallet/alice/balance
```

Submitting a transaction places it in the mempool. It does not change a confirmed balance until the transaction is included in a block. Transactions are mined automatically when the mempool reaches the node's block-size threshold, or explicitly with the mining endpoint.

## REST API

### Network

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/status` | Summary for every configured node |
| `GET` | `/consensus` | Compare node heights and tip hashes |
| `GET` | `/chain/valid` | Validate every configured chain |

### Wallets and Transactions

| Method | Endpoint | Description |
| --- | --- | --- |
| `POST` | `/wallet/create` | Create a wallet with `{ "name": "alice" }` |
| `GET` | `/wallet/list` | List wallet names and addresses |
| `GET` | `/wallet/{name}` | Get wallet metadata |
| `GET` | `/wallet/{name}/balance` | Get a wallet's confirmed balance and UTXO count |
| `POST` | `/tx/send` | Build, sign, and submit a UTXO transfer |

Transaction body:

```json
{
  "sender": "miner_node",
  "recipient": "alice",
  "amount": 10,
  "fee": 0.5,
  "node_port": 8123
}
```

`fee` and `node_port` are optional. The sender and recipient must already exist in the wallet store.

### Node Inspection and Mining

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/node/{port}/status` | Node health, height, mining state, and UTXO count |
| `POST` | `/node/{port}/mine` | Force the node to mine its pending transactions |
| `GET` | `/node/{port}/mempool` | Inspect pending transactions |
| `GET` | `/node/{port}/chain` | Return the serialized chain |
| `GET` | `/node/{port}/chain/height` | Return chain height |
| `GET` | `/node/{port}/block/{index}` | Inspect one block |
| `GET` | `/node/{port}/peers` | Inspect known peers and connection state |
| `POST` | `/node/{port}/peers/add` | Add a peer with `{ "host": "127.0.0.1", "port": 8124 }` |
| `GET` | `/node/{port}/balance/{address}` | Query an address balance |
| `GET` | `/node/{port}/utxos/{address}` | List an address's UTXOs |

Merkle endpoints are also available at `/node/{port}/block/{index}/merkle`, `/node/{port}/block/{index}/proof/{tx_index}`, and `/merkle/verify`.

## Persistence and Wallet Security

Runtime state is stored under `data/`:

- `wallets.json`: wallet keys and addresses
- `chain_{port}.json`: serialized blocks
- `utxo_{port}.json`: current UTXO set
- `peers_{port}.json`: peer discovery state

These files are ignored by Git. A fresh checkout creates a new miner wallet on first startup. Keep `data/wallets.json` private: it contains private keys stored in a local JSON file. Do not use these wallets for real funds.

## Docker

Build and run the single-node image:

```powershell
docker build -t quantum-chain .
docker run --rm -p 9000:9000 -p 8000:8000 quantum-chain
```

The container reads `PORT` for the REST API. Override settings with `-e`, for example:

```powershell
docker run --rm `
  -p 9010:9010 -p 8123:8123 `
  -e PORT=9010 `
  -e WS_PORT=8123 `
  quantum-chain
```

For persistent local data, mount the data directory with `-v`.

## Development

Run the complete test suite:

```powershell
python -m unittest discover -v
```

Compile the project modules:

```powershell
python -m py_compile api.py block.py chain.py cli.py main.py main_local.py merkle.py message.py node.py peer_manager.py storage.py Transaction.py utxo.py wallet.py
```

The repository includes configuration, wallet-signing, API mining, and mempool regression tests.

## Project Layout

| Path | Role |
| --- | --- |
| `main.py` | Single-node runtime entry point |
| `main_local.py` | Multi-node local development entry point |
| `node.py` | WebSocket node, mempool, peer, and mining logic |
| `chain.py` | Blocks, proof of work, chain validation, and rewards |
| `Transaction.py` | Signed UTXO transaction construction and validation |
| `wallet.py` | Wallet key generation, signing, verification, and addresses |
| `api.py` | REST API routes |
| `storage.py` | Chain and wallet persistence |
| `test_*.py` | Regression tests |
