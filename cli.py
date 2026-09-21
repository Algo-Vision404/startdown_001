# cli.py
#
# Interactive command-line interface for the quantum chain network.
#
# Runs as a separate asyncio task alongside the node network.
# Reads commands from stdin, executes them against the live node
# state, and prints results.
#
# Commands:
#
#   wallet create <name>
#       Generate a new ML-DSA-65 wallet and store it under <name>.
#
#   wallet list
#       Print all stored wallet names and their addresses.
#
#   wallet balance <name>
#       Compute the balance of a wallet from the UTXO set.
#
#   tx send <sender_name> <recipient_name> <amount> [fee] [node_port]
#       Sign and submit a UTXO transfer. Defaults to the first node
#       and a fee of 0.
#
#   chain status
#       Print height, validity, mempool size, and peers for all nodes.
#
#   chain show [node_port]
#       Print all blocks and a summary of their transactions.
#
#   chain verify [node_port]
#       Run full chain validation and report the result.
#
#   block show <index> [node_port]
#       Print a specific block and all its transactions.
#
#   mempool [node_port]
#       Print all pending transactions in a node's mempool.
#
#   consensus
#       Check whether all nodes have the same height and tip hash.
#
#   help
#       Print this command list.
#
#   exit
#       Shut down the network and quit.

import asyncio
import sys

from wallet import QuantumWallet
from Transaction import Transaction, TransactionError
from storage import WalletStore


PROMPT = "qchain> "


def _outputs_summary(tx) -> str:
    """One-line human-readable summary of a transaction's outputs."""
    if not tx.outputs:
        return "(no outputs)"
    parts = [f"{o['address'][:14]}...:{o['amount']}" for o in tx.outputs]
    return ", ".join(parts)


class CLI:

    def __init__(self, nodes: list, wallet_store: WalletStore):
        self.nodes        = nodes
        self.wallet_store = wallet_store
        self._node_map    = {node.port: node for node in nodes}
        self._running     = True

    # ─────────────────────────────────────────────────────────
    # Entry point
    # ─────────────────────────────────────────────────────────

    async def run(self):
        """
        Read commands from stdin in a loop.

        Uses asyncio.get_event_loop().run_in_executor so that blocking
        input() calls do not freeze the event loop. The network keeps
        processing messages while we wait for user input.
        """
        print("\nQuantum Chain CLI ready. Type 'help' for commands.\n")
        loop = asyncio.get_event_loop()

        while self._running:
            try:
                raw = await loop.run_in_executor(None, self._prompt)
                if raw is None:
                    break
                line = raw.strip()
                if not line:
                    continue
                await self._dispatch(line)
            except (KeyboardInterrupt, EOFError):
                break
            except Exception as e:
                print(f"error: {e}")

    def _prompt(self) -> str | None:
        try:
            return input(PROMPT)
        except EOFError:
            return None

    # ─────────────────────────────────────────────────────────
    # Dispatch
    # ─────────────────────────────────────────────────────────

    async def _dispatch(self, line: str):
        parts = line.split()
        if not parts:
            return

        cmd = parts[0].lower()

        if cmd == "help":
            self._help()

        elif cmd == "exit":
            self._running = False
            print("shutting down.")

        elif cmd == "wallet":
            await self._wallet_cmd(parts[1:])

        elif cmd == "tx":
            await self._tx_cmd(parts[1:])

        elif cmd == "chain":
            await self._chain_cmd(parts[1:])

        elif cmd == "block":
            await self._block_cmd(parts[1:])

        elif cmd == "mempool":
            self._mempool_cmd(parts[1:])

        elif cmd == "consensus":
            self._consensus_cmd()

        else:
            print(f"unknown command: '{cmd}'. type 'help'.")

    # ─────────────────────────────────────────────────────────
    # Wallet commands
    # ─────────────────────────────────────────────────────────

    async def _wallet_cmd(self, args: list):
        if not args:
            print("usage: wallet <create|list|balance> [args]")
            return

        sub = args[0].lower()

        if sub == "create":
            if len(args) < 2:
                print("usage: wallet create <name>")
                return
            name = args[1]
            try:
                wallet = self.wallet_store.create(name)
                print(f"wallet created")
                print(f"  name      : {name}")
                print(f"  address   : {wallet.address}")
                print(f"  algorithm : ML-DSA-65 (Dilithium3)")
                print(f"  pubkey_sz : {len(wallet.public_key)} bytes")
            except ValueError as e:
                print(f"error: {e}")

        elif sub == "list":
            names = self.wallet_store.list_wallets()
            if not names:
                print("no wallets found. use 'wallet create <name>'.")
                return
            print(f"\n{'NAME':<20} {'ADDRESS'}")
            print("─" * 70)
            for name in names:
                w = self.wallet_store.get(name)
                print(f"{name:<20} {w.address}")

        elif sub == "balance":
            if len(args) < 2:
                print("usage: wallet balance <name>")
                return
            name   = args[1]
            wallet = self.wallet_store.get(name)
            if not wallet:
                print(f"wallet '{name}' not found.")
                return

            # Use node 0 as the source of truth for balance queries.
            # In a consistent network all nodes have the same chain,
            # so it does not matter which node we ask.
            node    = self.nodes[0]
            balance = node.balance(wallet.address)
            print(f"wallet  : {name}")
            print(f"address : {wallet.address}")
            print(f"balance : {balance}")

        else:
            print(f"unknown wallet sub-command: '{sub}'")

    # ─────────────────────────────────────────────────────────
    # Transaction commands
    # ─────────────────────────────────────────────────────────

    async def _tx_cmd(self, args: list):
        if not args or args[0].lower() != "send":
            print("usage: tx send <sender> <recipient> <amount> [fee] [node_port]")
            return

        args = args[1:]
        if len(args) < 3:
            print("usage: tx send <sender> <recipient> <amount> [fee] [node_port]")
            return

        sender_name    = args[0]
        recipient_name = args[1]

        try:
            amount = float(args[2])
        except ValueError:
            print(f"invalid amount: '{args[2]}'")
            return

        # Optional 4th arg is fee, optional 5th arg is node_port.
        # Both trailing args are optional so we detect node_port by
        # checking whether the last token looks like a known port.
        fee       = 0.0
        node_port = self.nodes[0].port

        remaining = args[3:]
        if len(remaining) == 1:
            # Ambiguous: could be fee or node_port. If it matches a
            # live node port, treat it as node_port; otherwise it's fee.
            try:
                as_int = int(remaining[0])
            except ValueError:
                as_int = None
            if as_int is not None and as_int in self._node_map:
                node_port = as_int
            else:
                try:
                    fee = float(remaining[0])
                except ValueError:
                    print(f"invalid fee: '{remaining[0]}'")
                    return
        elif len(remaining) >= 2:
            try:
                fee = float(remaining[0])
            except ValueError:
                print(f"invalid fee: '{remaining[0]}'")
                return
            try:
                node_port = int(remaining[1])
            except ValueError:
                print(f"invalid node_port: '{remaining[1]}'")
                return

        node = self._node_map.get(node_port)
        if not node:
            print(f"no node on port {node_port}. available: {list(self._node_map.keys())}")
            return

        sender    = self.wallet_store.get(sender_name)
        recipient = self.wallet_store.get(recipient_name)

        if not sender:
            print(f"sender wallet '{sender_name}' not found.")
            return
        if not recipient:
            print(f"recipient wallet '{recipient_name}' not found.")
            return
        if amount <= 0:
            print("amount must be positive.")
            return
        if fee < 0:
            print("fee cannot be negative.")
            return

        try:
            tx = Transaction.transfer(
                sender_wallet      = sender,
                utxo_set           = node.chain.utxo_set,
                recipient_address  = recipient.address,
                amount             = amount,
                fee                = fee
            )
        except TransactionError as e:
            print(f"error: {e}")
            return

        await node.submit_transaction(tx)

        print(f"transaction submitted to node {node_port}")
        print(f"  tx_id       : {tx.tx_id[:32]}...")
        print(f"  from        : {sender_name} ({sender.address[:24]}...)")
        print(f"  to          : {recipient_name} ({recipient.address[:24]}...)")
        print(f"  amount      : {amount}")
        print(f"  fee         : {fee}")
        print(f"  inputs used : {len(tx.inputs)}")
        print(f"  outputs     : {_outputs_summary(tx)}")
        print(f"  sig_bytes   : {len(tx.signature)}")
        print(f"  valid       : {tx.is_valid()}")

    # ─────────────────────────────────────────────────────────
    # Chain commands
    # ─────────────────────────────────────────────────────────

    async def _chain_cmd(self, args: list):
        if not args:
            print("usage: chain <status|show|verify> [args]")
            return

        sub = args[0].lower()

        if sub == "status":
            print(
                f"\n{'PORT':<8} {'HEIGHT':<8} {'VALID':<8} "
                f"{'DIFF':<6} {'MEMPOOL':<10} {'MINING':<8} {'TIP HASH':<36} PEERS"
            )
            print("─" * 100)
            for node in self.nodes:
                s = node.status()
                print(
                    f"{s['port']:<8} "
                    f"{s['height']:<8} "
                    f"{str(s['chain_valid']):<8} "
                    f"{s['difficulty']:<6} "
                    f"{s['mempool']:<10} "
                    f"{str(s['mining']):<8} "
                    f"{s['tip_hash']:<36} "
                    f"{s['peers']}"
                )

        elif sub == "show":
            node_port = int(args[1]) if len(args) > 1 else self.nodes[0].port
            node      = self._node_map.get(node_port)
            if not node:
                print(f"no node on port {node_port}")
                return
            print(f"\nchain on node {node_port}  height={node.chain.height()}\n")
            for block in node.chain.chain:
                print(repr(block))
                if block.transactions:
                    for tx in block.transactions:
                        kind = "COINBASE" if tx.is_coinbase else tx.sender[:18] + "..."
                        print(
                            f"   {tx.tx_id[:20]}...  "
                            f"from={kind}  "
                            f"inputs={len(tx.inputs)}  "
                            f"outputs=[{_outputs_summary(tx)}]  "
                            f"fee={tx.fee}"
                        )
                else:
                    print("   (no transactions — genesis block)")

        elif sub == "verify":
            node_port = int(args[1]) if len(args) > 1 else self.nodes[0].port
            node      = self._node_map.get(node_port)
            if not node:
                print(f"no node on port {node_port}")
                return
            result = node.chain.is_valid()
            print(f"node {node_port} chain valid : {result}")

        else:
            print(f"unknown chain sub-command: '{sub}'")

    # ─────────────────────────────────────────────────────────
    # Block commands
    # ─────────────────────────────────────────────────────────

    async def _block_cmd(self, args: list):
        if not args or args[0].lower() != "show":
            print("usage: block show <index> [node_port]")
            return

        args = args[1:]
        if not args:
            print("usage: block show <index> [node_port]")
            return

        try:
            index = int(args[0])
        except ValueError:
            print(f"invalid block index: '{args[0]}'")
            return

        node_port = int(args[1]) if len(args) > 1 else self.nodes[0].port
        node      = self._node_map.get(node_port)
        if not node:
            print(f"no node on port {node_port}")
            return

        if index >= node.chain.height():
            print(f"block {index} does not exist. chain height = {node.chain.height()}")
            return

        block = node.chain.chain[index]
        print(f"\nblock {block.index}")
        print(f"  hash             : {block.hash}")
        print(f"  previous_hash    : {block.previous_hash}")
        print(f"  difficulty       : {block.difficulty}")
        print(f"  nonce            : {block.nonce}")
        print(f"  timestamp        : {block.timestamp}")
        print(f"  transactions     : {block.transaction_count()}")
        print(f"  internally valid : {block.is_internally_valid()}")

        if block.transactions:
            print()
            for i, tx in enumerate(block.transactions):
                print(f"  tx[{i}]")
                print(f"    tx_id       : {tx.tx_id}")
                print(f"    is_coinbase : {tx.is_coinbase}")
                print(f"    sender      : {tx.sender}")
                print(f"    inputs      : {tx.inputs}")
                print(f"    outputs     : {tx.outputs}")
                print(f"    fee         : {tx.fee}")
                print(f"    timestamp   : {tx.timestamp}")
                print(f"    sig_bytes   : {len(tx.signature)}")
                print(f"    valid       : {tx.is_valid()}")

    # ─────────────────────────────────────────────────────────
    # Mempool command
    # ─────────────────────────────────────────────────────────

    def _mempool_cmd(self, args: list):
        node_port = int(args[0]) if args else self.nodes[0].port
        node      = self._node_map.get(node_port)
        if not node:
            print(f"no node on port {node_port}")
            return

        if not node.mempool:
            print(f"node {node_port} mempool is empty")
            return

        print(f"\nnode {node_port} mempool  ({len(node.mempool)} pending)\n")
        print(f"{'TX_ID':<24} {'SENDER':<22} {'FEE':<10} OUTPUTS")
        print("─" * 100)
        for tx in node.mempool:
            sender_disp = "COINBASE" if tx.is_coinbase else tx.sender[:20]
            print(
                f"{tx.tx_id[:22]:<24} "
                f"{sender_disp:<22} "
                f"{tx.fee:<10} "
                f"{_outputs_summary(tx)}"
            )

    # ─────────────────────────────────────────────────────────
    # Consensus command
    # ─────────────────────────────────────────────────────────

    def _consensus_cmd(self):
        heights    = {node.port: node.chain.height() for node in self.nodes}
        tip_hashes = {node.port: node.chain.chain[-1].hash for node in self.nodes}

        unique_heights = set(heights.values())
        unique_tips    = set(tip_hashes.values())

        print(f"\nconsensus check across {len(self.nodes)} nodes\n")
        print(f"{'PORT':<8} {'HEIGHT':<8} TIP HASH")
        print("─" * 60)
        for node in self.nodes:
            print(
                f"{node.port:<8} "
                f"{node.chain.height():<8} "
                f"{node.chain.chain[-1].hash[:32]}..."
            )

        print()
        print(f"all same height : {len(unique_heights) == 1}   {heights}")
        print(f"all same tip    : {len(unique_tips) == 1}")

        if len(unique_heights) == 1 and len(unique_tips) == 1:
            print(f"consensus       : reached")
        else:
            print(f"consensus       : not reached — nodes diverged")

    # ─────────────────────────────────────────────────────────
    # Help
    # ─────────────────────────────────────────────────────────

    def _help(self):
        print("""
commands:
  wallet create <name>                       create a new wallet
  wallet list                                list all wallets
  wallet balance <name>                      show wallet balance

  tx send <sender> <recipient> <amount>      submit a transaction
         [fee] [node_port]                   both trailing args optional

  chain status                               all node heights, peers, mempool sizes
  chain show [node_port]                     print full chain with transactions
  chain verify [node_port]                   run full chain validation

  block show <index> [node_port]             print a specific block in detail

  mempool [node_port]                        print pending transactions

  consensus                                  check if all nodes agree

  help                                       print this list
  exit                                       shut down and quit
        """)