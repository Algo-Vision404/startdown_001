# node.py — replace these methods only. All others remain unchanged.

    BLOCK_SIZE = 2

    # Replace _on_transaction with this version
    async def _on_transaction(self, tx: Transaction):
        if tx.tx_id in self.seen_tx_ids:
            return

        if not tx.is_valid():
            logging.warning(f"[{self.port}] rejected invalid tx {tx.tx_id[:16]}")
            return

        # UTXO validation at mempool entry
        if not tx.is_coinbase:
            if not tx.validate_against_utxo_set(self.chain.utxo_set):
                logging.warning(
                    f"[{self.port}] rejected tx {tx.tx_id[:16]}: "
                    f"UTXO validation failed"
                )
                return

        self.seen_tx_ids.add(tx.tx_id)
        self.mempool.append(tx)

        # Keep mempool sorted by fee descending so highest-fee
        # transactions are always at the front and get mined first.
        self.mempool.sort(key=lambda t: t.fee, reverse=True)

        logging.info(
            f"[{self.port}] accepted tx {tx.tx_id[:16]}... "
            f"fee={tx.fee} mempool={len(self.mempool)}"
        )

        await self._broadcast(
            build(MessageType.TRANSACTION, self.port, serialize_transaction(tx))
        )

        if len(self.mempool) >= self.BLOCK_SIZE and not self.mining:
            asyncio.create_task(self._mine())

    # Replace _mine with this version
    async def _mine(self):
        self.mining  = True
        tip_hash     = self.chain.chain[-1].hash
        to_mine      = self.mempool[:self.BLOCK_SIZE]

        # Use this node's first wallet as miner address if available,
        # otherwise use a placeholder. In production each node would
        # have a dedicated mining wallet configured at startup.
        miner_address = getattr(self, "miner_address", "MINER_UNSET")

        logging.info(
            f"[{self.port}] mining {len(to_mine)} txs "
            f"tip={tip_hash[:16]}... "
            f"miner={miner_address[:24]}..."
        )

        try:
            loop  = asyncio.get_event_loop()
            block = await loop.run_in_executor(
                None,
                self.chain.mine_block,
                to_mine,
                miner_address
            )

            if block.previous_hash != self.chain.chain[-1].hash:
                logging.info(f"[{self.port}] mined block stale, discarding")
                return

            self.chain.append_block(block)
            self.seen_block_hashes.add(block.hash)
            self._save_chain()

            mined_ids    = {tx.tx_id for tx in to_mine}
            self.mempool = [
                tx for tx in self.mempool
                if tx.tx_id not in mined_ids
            ]

            reward = block.transactions[0].total_output() if block.transactions else 0
            logging.info(
                f"[{self.port}] mined block {block.index} "
                f"nonce={block.nonce} "
                f"reward={reward} "
                f"hash={block.hash[:16]}... "
                f"height={self.chain.height()}"
            )

            await self._broadcast(
                build(MessageType.BLOCK, self.port, serialize_block(block))
            )

        except Exception as e:
            logging.error(f"[{self.port}] mining error: {e}")
        finally:
            self.mining = False

    # Replace balance() with this version
    def balance(self, address: str) -> float:
        """O(1) balance lookup from the UTXO set."""
        return self.chain.utxo_set.balance(address)

    def utxos_for(self, address: str) -> list:
        return [u.to_dict() for u in self.chain.utxo_set.utxos_for(address)]