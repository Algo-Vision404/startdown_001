import json
from wallet import QuantumWallet, WalletError
from transaction import Transaction, TransactionError


def separator(label: str = "") -> None:
    width = 60
    if label:
        print(f"\n{'─' * 4} {label} {'─' * (width - len(label) - 6)}")
    else:
        print("─" * width)


def run() -> None:

    separator("Wallet Generation")

    alice = QuantumWallet()
    bob   = QuantumWallet()

    print(alice)
    print(bob)


    separator("Transaction Signing")

    tx = Transaction(
        sender_address    = alice.address,
        recipient_address = bob.address,
        amount            = 50.0
    )

    tx.sign(alice)
    print(repr(tx))


    separator("Transaction Verification")

    result = tx.is_valid()
    print(f"valid            : {result}")
    print(json.dumps(tx.to_dict(), indent=4))


    separator("Forgery Attempt")

    # Bob constructs a transaction claiming to be Alice,
    # then signs it with his own private key.
    # This simulates an attacker who knows Alice's address
    # but does not have her private key.

    forged = Transaction(
        sender_address    = alice.address,
        recipient_address = bob.address,
        amount            = 999999.0
    )

    # Manually attach Bob's key and signature
    # bypassing the address check in sign()
    forged.sender_public_key = bob.public_key
    forged.signature         = bob.sign(forged.to_bytes())
    forged.tx_id             = "arbitrary"

    forgery_caught = not forged.is_valid()
    print(f"forgery detected : {forgery_caught}")

    # Which check caught it?
    # _derive_address(bob.public_key) != alice.address
    # The address mismatch fails before signature verification even runs.


    separator("Wallet Persistence")

    alice.save("alice.json")

    alice_reloaded = QuantumWallet.load("alice.json")
    print(f"address match    : {alice.address == alice_reloaded.address}")

    tx2 = Transaction(
        sender_address    = alice_reloaded.address,
        recipient_address = bob.address,
        amount            = 10.0
    )
    tx2.sign(alice_reloaded)
    print(f"post-reload sign : {tx2.is_valid()}")


    separator("Done")
    print("Week 1-2 complete. Next: Block and Chain.")


if __name__ == "__main__":
    run()