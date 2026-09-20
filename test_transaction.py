import unittest

from Transaction import Transaction
from utxo import UTXO, UTXOSet
from wallet import QuantumWallet


class TestTransactionValidation(unittest.TestCase):
    def test_declared_fee_must_match_input_output_remainder(self):
        wallet = QuantumWallet()
        source_id = "a" * 64
        utxo_set = UTXOSet()
        utxo_set.add(UTXO(source_id, 0, wallet.address, 10.0))

        transaction = Transaction(
            sender_address=wallet.address,
            inputs=[{"tx_id": source_id, "index": 0}],
            outputs=[{"address": "recipient", "amount": 9.0}],
            fee=5.0,
        )
        transaction.sign(wallet)

        self.assertFalse(transaction.validate_against_utxo_set(utxo_set))

    def test_transfer_fee_matches_change(self):
        wallet = QuantumWallet()
        source_id = "b" * 64
        utxo_set = UTXOSet()
        utxo_set.add(UTXO(source_id, 0, wallet.address, 10.0))

        transaction = Transaction.transfer(
            sender_wallet=wallet,
            utxo_set=utxo_set,
            recipient_address="recipient",
            amount=8.0,
            fee=1.0,
        )

        self.assertTrue(transaction.validate_against_utxo_set(utxo_set))

    def test_non_positive_output_is_rejected(self):
        wallet = QuantumWallet()
        source_id = "c" * 64
        utxo_set = UTXOSet()
        utxo_set.add(UTXO(source_id, 0, wallet.address, 10.0))

        transaction = Transaction(
            sender_address=wallet.address,
            inputs=[{"tx_id": source_id, "index": 0}],
            outputs=[{"address": "recipient", "amount": -1.0}],
            fee=11.0,
        )
        transaction.sign(wallet)

        self.assertFalse(transaction.validate_against_utxo_set(utxo_set))

    def test_tampered_transaction_id_is_rejected(self):
        wallet = QuantumWallet()
        source_id = "d" * 64
        utxo_set = UTXOSet()
        utxo_set.add(UTXO(source_id, 0, wallet.address, 10.0))

        transaction = Transaction.transfer(
            sender_wallet=wallet,
            utxo_set=utxo_set,
            recipient_address="recipient",
            amount=8.0,
            fee=1.0,
        )
        transaction.tx_id = "e" * 64

        self.assertFalse(transaction.is_valid())
        self.assertFalse(transaction.validate_against_utxo_set(utxo_set))


if __name__ == "__main__":
    unittest.main()
