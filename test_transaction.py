import math
import unittest

from Transaction import Transaction, TransactionError
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


class TestTransferMany(unittest.TestCase):
    def test_pays_every_recipient_in_one_transaction(self):
        wallet = QuantumWallet()
        source_id = "1" * 64
        utxo_set = UTXOSet()
        utxo_set.add(UTXO(source_id, 0, wallet.address, 30.0))

        recipients = [
            {"address": "alice", "amount": 10.0},
            {"address": "bob", "amount": 8.0},
        ]
        transaction = Transaction.transfer_many(
            sender_wallet=wallet,
            utxo_set=utxo_set,
            recipients=recipients,
            fee=1.0,
        )

        self.assertTrue(transaction.validate_against_utxo_set(utxo_set))
        self.assertEqual(len(transaction.inputs), 1)  # one UTXO covers it all
        paid = {o["address"]: o["amount"] for o in transaction.outputs}
        self.assertEqual(paid["alice"], 10.0)
        self.assertEqual(paid["bob"], 8.0)
        # Change: 30 - 10 - 8 - 1 (fee) = 11, returned to the sender.
        self.assertEqual(paid[wallet.address], 11.0)

    def test_no_change_output_when_amounts_use_the_full_balance(self):
        wallet = QuantumWallet()
        source_id = "2" * 64
        utxo_set = UTXOSet()
        utxo_set.add(UTXO(source_id, 0, wallet.address, 20.0))

        recipients = [
            {"address": "alice", "amount": 10.0},
            {"address": "bob", "amount": 9.0},
        ]
        transaction = Transaction.transfer_many(
            sender_wallet=wallet,
            utxo_set=utxo_set,
            recipients=recipients,
            fee=1.0,
        )

        self.assertTrue(transaction.validate_against_utxo_set(utxo_set))
        self.assertEqual(len(transaction.outputs), 2)  # no change output

    def test_selects_multiple_inputs_when_one_utxo_is_not_enough(self):
        wallet = QuantumWallet()
        utxo_set = UTXOSet()
        utxo_set.add(UTXO("3" * 64, 0, wallet.address, 5.0))
        utxo_set.add(UTXO("4" * 64, 0, wallet.address, 5.0))
        utxo_set.add(UTXO("5" * 64, 0, wallet.address, 5.0))

        recipients = [
            {"address": "alice", "amount": 6.0},
            {"address": "bob", "amount": 5.0},
        ]
        transaction = Transaction.transfer_many(
            sender_wallet=wallet,
            utxo_set=utxo_set,
            recipients=recipients,
            fee=0.0,
        )

        self.assertTrue(transaction.validate_against_utxo_set(utxo_set))
        self.assertGreaterEqual(len(transaction.inputs), 2)

    def test_rejects_empty_recipient_list(self):
        wallet = QuantumWallet()
        utxo_set = UTXOSet()
        utxo_set.add(UTXO("6" * 64, 0, wallet.address, 10.0))

        with self.assertRaises(TransactionError):
            Transaction.transfer_many(
                sender_wallet=wallet,
                utxo_set=utxo_set,
                recipients=[],
                fee=0.0,
            )

    def test_rejects_non_positive_recipient_amount(self):
        wallet = QuantumWallet()
        utxo_set = UTXOSet()
        utxo_set.add(UTXO("7" * 64, 0, wallet.address, 10.0))

        with self.assertRaises(TransactionError):
            Transaction.transfer_many(
                sender_wallet=wallet,
                utxo_set=utxo_set,
                recipients=[{"address": "alice", "amount": 0.0}],
                fee=0.0,
            )

    def test_rejects_non_finite_recipient_amount(self):
        wallet = QuantumWallet()
        utxo_set = UTXOSet()
        utxo_set.add(UTXO("8" * 64, 0, wallet.address, 10.0))

        with self.assertRaises(TransactionError):
            Transaction.transfer_many(
                sender_wallet=wallet,
                utxo_set=utxo_set,
                recipients=[{"address": "alice", "amount": math.nan}],
                fee=0.0,
            )

    def test_insufficient_funds_for_combined_total_is_rejected(self):
        wallet = QuantumWallet()
        utxo_set = UTXOSet()
        utxo_set.add(UTXO("9" * 64, 0, wallet.address, 10.0))

        with self.assertRaises(TransactionError):
            Transaction.transfer_many(
                sender_wallet=wallet,
                utxo_set=utxo_set,
                recipients=[
                    {"address": "alice", "amount": 6.0},
                    {"address": "bob", "amount": 6.0},
                ],  # 12 total, only 10 available
                fee=0.0,
            )

    def test_transfer_is_a_single_recipient_case_of_transfer_many(self):
        wallet = QuantumWallet()
        source_id = "0" * 64
        utxo_set = UTXOSet()
        utxo_set.add(UTXO(source_id, 0, wallet.address, 10.0))

        single = Transaction.transfer(
            sender_wallet=wallet,
            utxo_set=utxo_set,
            recipient_address="recipient",
            amount=8.0,
            fee=1.0,
        )

        self.assertEqual(len(single.outputs), 2)  # recipient + change
        self.assertTrue(single.validate_against_utxo_set(utxo_set))


if __name__ == "__main__":
    unittest.main()