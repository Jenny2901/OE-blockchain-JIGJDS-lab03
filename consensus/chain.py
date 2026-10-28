"""Ledger: transactions, account state, blocks and heaviest-chain fork choice -- my own implementation."""
import hashlib
import json
import time
from dataclasses import dataclass, replace

MAX_TARGET = 2**256 - 1              # giá trị băm lớn nhất có thể
GENESIS_TARGET = MAX_TARGET >> 16    # ngưỡng ứng với độ khó 1 (khoảng 65 000 lần băm)


def sha256d(data: bytes) -> bytes:
    """Double SHA-256, as in Bitcoin (same as Lab 01)."""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


@dataclass(frozen=True)
class Transaction:
    """A transfer: who pays whom, how much, with which nonce and fee."""
    sender: str
    recipient: str
    amount: int
    nonce: int
    fee: int = 0

    @property
    def txid(self) -> str:
        """Hash of all fields in a fixed order, so every node computes the same id."""
        data = json.dumps(
            {"sender": self.sender, "recipient": self.recipient, "amount": self.amount,
             "nonce": self.nonce, "fee": self.fee},
            sort_keys=True,
        )
        return sha256d(data.encode()).hex()


class State:
    """Balances and nonces of every account (Ethereum-style account model)."""

    def __init__(self, balances=None):
        self.balances = dict(balances or {})     # tên tài khoản -> số dư
        self.nonces = {}                         # tên tài khoản -> nonce tiếp theo

    def copy(self):
        clone = State(self.balances)
        clone.nonces = dict(self.nonces)
        return clone

    def balance_of(self, account: str) -> int:
        return self.balances.get(account, 0)    # chưa có tài khoản -> 0

    def nonce_of(self, account: str) -> int:
        return self.nonces.get(account, 0)

    def validate(self, tx: Transaction):
        """Return None if the transaction can be applied, otherwise the reason."""
        if tx.amount < 0 or tx.fee < 0:
            return "negative amount"
        if self.balance_of(tx.sender) < tx.amount + tx.fee:      # không đủ tiền
            return "insufficient balance"
        expected = self.nonce_of(tx.sender)
        if tx.nonce != expected:                                  # nhảy cóc hoặc lặp lại
            return f"nonce mismatch: expected {expected}, got {tx.nonce}"
        return None                                               # hợp lệ

    def apply(self, tx: Transaction) -> None:
        """Carry out a transaction. The fee is taken from the sender but NOT given to the recipient."""
        reason = self.validate(tx)
        if reason is not None:
            raise ValueError(f"invalid transaction: {reason}")
        self.balances[tx.sender] = self.balance_of(tx.sender) - tx.amount - tx.fee
        self.balances[tx.recipient] = self.balance_of(tx.recipient) + tx.amount
        self.nonces[tx.sender] = tx.nonce + 1

    def apply_block(self, block, reward: int = 0) -> None:
        """Apply every transaction in a block, then pay the miner the reward plus all fees."""
        for tx in block.txs:
            self.apply(tx)
        if block.miner:
            fees = sum(tx.fee for tx in block.txs)
            self.balances[block.miner] = self.balance_of(block.miner) + reward + fees


# --- written in lesson 1b ---
class Block: pass
class Chain: pass
def genesis_block(difficulty=1): raise NotImplementedError
def make_block(chain, **kwargs): raise NotImplementedError