import json
from wallet import QuantumWallet, WalletError
from transaction import Transaction, TransactionError
from block import Block, BlockError
from chain import Blockchain, ChainError


def separator(label: str = "") -> None:
    width = 60
    if label:
        print(f"\n{'─' * 4} {label} {'─' * (width - len(label) - 6)}")
    else:
        print("─" * width)


def make_signed_transaction(
    sender    : QuantumWallet,
    recipient : QuantumWallet,
    amount    : float
) -> Transaction:
    tx = Transaction(
        sender_address    = sender.address,
        recipient_address = recipient.address,
        amount            = amount
    )
    tx.sign(sender)
    return tx


def run() -> None:

    # ── Wallets ───────────────────────────────────────────────
    separator("Wallet Generation")

    alice = QuantumWallet()
    bob   = QuantumWallet()
    carol = QuantumWallet()

    print(alice)
    print(bob)
    print(carol)


    # ── Chain bootstrap ───────────────────────────────────────
    separator("Genesis Block")

    chain = Blockchain()

    print(repr(chain.chain[0]))
    print(f"genesis hash     : {chain.chain[0].hash}")
    print(f"genesis nonce    : {chain.chain[0].nonce}")
    print(f"target satisfied : {chain.chain[0].hash.startswith('0' * Blockchain.DIFFICULTY)}")


    # ── Block 1 ───────────────────────────────────────────────
    separator("Block 1 — Alice sends to Bob and Carol")

    tx1 = make_signed_transaction(alice, bob,   50.0)
    tx2 = make_signed_transaction(alice, carol, 25.0)

    block1 = chain.add_block([tx1, tx2])

    print(repr(block1))
    print(f"nonce            : {block1.nonce}")
    print(f"transactions     : {block1.transaction_count()}")
    print(f"hash             : {block1.hash}")


    # ── Block 2 ───────────────────────────────────────────────
    separator("Block 2 — Bob sends to Carol")

    tx3 = make_signed_transaction(bob, carol, 10.0)

    block2 = chain.add_block([tx3])

    print(repr(block2))
    print(f"nonce            : {block2.nonce}")
    print(f"previous_hash    : {block2.previous_hash[:32]}...")
    print(f"block1 hash      : {block1.hash[:32]}...")
    print(f"hashes match     : {block2.previous_hash == block1.hash}")


    # ── Full chain validation ─────────────────────────────────
    separator("Full Chain Validation")

    print(f"chain height     : {chain.height()}")
    result = chain.is_valid()
    print(f"chain valid      : {result}")
    chain.print_chain()


    # ── Tamper test 1: modify a transaction amount ────────────
    separator("Tamper Test 1 — Modify Transaction Amount in Block 1")

    print("before tamper    : chain valid =", chain.is_valid())

    # An attacker reaches into block 1 and changes the amount
    # on transaction 1 from 50.0 to 50000.0.
    # The block hash was computed over amount=50.0.
    # After this change the block contents no longer match the hash.
    chain.chain[1].transactions[0].amount = 50000.0

    print("after tamper     : chain valid =", chain.is_valid())

    # Restore so we can run the next test cleanly
    chain.chain[1].transactions[0].amount = 50.0


    # ── Tamper test 2: modify the block hash directly ─────────
    separator("Tamper Test 2 — Overwrite Block Hash Directly")

    print("before tamper    : chain valid =", chain.is_valid())

    # An attacker overwrites the hash field of block 1 with
    # a forged value. This breaks block 2's linkage check
    # because block 2's previous_hash no longer matches.
    original_hash = chain.chain[1].hash
    chain.tamper(1, "hash", "a" * 64)

    print("after tamper     : chain valid =", chain.is_valid())

    chain.chain[1].hash = original_hash


    # ── Tamper test 3: inject an invalid transaction ──────────
    separator("Tamper Test 3 — Inject Unsigned Transaction into Block")

    print("before tamper    : chain valid =", chain.is_valid())

    # An attacker constructs a transaction that was never signed
    # and injects it directly into block 2's transaction list.
    # No private key was used. The signature field is empty.
    fake_tx = Transaction(
        sender_address    = alice.address,
        recipient_address = bob.address,
        amount            = 999.0
    )
    chain.chain[2].transactions.append(fake_tx)

    print("after tamper     : chain valid =", chain.is_valid())

    chain.chain[2].transactions.pop()


    # ── Reject block with invalid transaction ─────────────────
    separator("Reject Block — Unsigned Transaction Refused at Entry")

    unsigned_tx = Transaction(
        sender_address    = bob.address,
        recipient_address = carol.address,
        amount            = 5.0
    )

    try:
        chain.add_block([unsigned_tx])
        print("block added      : True  (BUG — should have been rejected)")
    except ChainError as e:
        print(f"block rejected   : True")
        print(f"reason           : {e}")


    # ── Final state ───────────────────────────────────────────
    separator("Final Chain State")

    print(repr(chain))
    print(f"height           : {chain.height()}")
    print(f"valid            : {chain.is_valid()}")

    separator("Done")
    print("Week 3-4 complete. Next: Multi-Node Network.")


if __name__ == "__main__":
    run()