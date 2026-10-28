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


@dataclass(frozen=True)
class Block:
    """A block header plus its transactions."""
    height: int
    prev_hash: str
    timestamp: float
    txs: tuple = ()
    difficulty: int = 1
    nonce: int = 0
    miner: str = ""

    @property
    def target(self) -> int:
        """Higher difficulty -> lower target -> harder to mine."""
        return GENESIS_TARGET // max(1, self.difficulty)

    @property
    def merkle_root(self) -> str:
        """One hash that stands for the whole transaction set."""
        if not self.txs:
            return "00" * 32
        joined = "".join(tx.txid for tx in self.txs)
        return sha256d(joined.encode()).hex()

    def header_bytes(self, nonce=None) -> bytes:
        """The fields that get hashed, in a fixed order."""
        if nonce is None:
            nonce = self.nonce
        header = {
            "height": self.height, "prev": self.prev_hash, "ts": round(self.timestamp, 6),
            "root": self.merkle_root, "diff": self.difficulty, "miner": self.miner,
            "nonce": nonce,
        }
        return json.dumps(header, sort_keys=True).encode()

    @property
    def hash(self) -> str:
        return sha256d(self.header_bytes()).hex()

    @property
    def work(self) -> int:
        """Expected number of hashes needed to find this block."""
        return MAX_TARGET // max(1, self.target)

    def is_valid_pow(self) -> bool:
        """Proof of work: the hash, read as a number, must be below the target."""
        return int.from_bytes(sha256d(self.header_bytes()), "big") < self.target

    def with_nonce(self, nonce: int):
        return replace(self, nonce=nonce)        # bản sao với nonce mới


def genesis_block(difficulty: int = 1) -> Block:
    """The fixed first block, the same on every node."""
    return Block(height=0, prev_hash="00" * 32, timestamp=0.0, difficulty=difficulty)


@dataclass
class ChainEntry:
    """A stored block plus the total work from genesis up to it."""
    block: Block
    total_work: int


class Chain:
    """A TREE of blocks. The canonical chain is the path with the most total work."""

    def __init__(self, genesis=None):
        if genesis is None:
            genesis = genesis_block()
        self.entries = {genesis.hash: ChainEntry(genesis, genesis.work)}   # mọi block, cả nhánh thua
        self.genesis_hash = genesis.hash
        self.tip_hash = genesis.hash                                         # đầu nhánh chính
        self.reorg_count = 0
        self.reorg_depths = []

    @property
    def tip(self) -> Block:
        return self.entries[self.tip_hash].block

    @property
    def height(self) -> int:
        return self.tip.height

    @property
    def total_work(self) -> int:
        return self.entries[self.tip_hash].total_work

    def has(self, block_hash: str) -> bool:
        return block_hash in self.entries

    def path_to(self, block_hash: str) -> list:
        """Blocks from genesis to block_hash: walk back through parents, then reverse."""
        path = []
        cursor = block_hash
        while cursor in self.entries:
            block = self.entries[cursor].block
            path.append(block)
            if cursor == self.genesis_hash:
                break
            cursor = block.prev_hash                 # lùi về block cha
        path.reverse()
        return path

    def canonical(self) -> list:
        """The main chain: genesis -> tip."""
        return self.path_to(self.tip_hash)

    def add_block(self, block: Block, *, check_pow: bool = True) -> bool:
        """Store a block. Return True if it became the new tip."""
        if block.hash in self.entries:               # đã có rồi
            return False
        parent = self.entries.get(block.prev_hash)
        if parent is None:                           # không biết block cha là ai
            raise KeyError(f"unknown parent for block at height {block.height}")
        if block.height != parent.block.height + 1:
            raise ValueError("height must be parent height + 1")
        if check_pow and not block.is_valid_pow():
            raise ValueError("invalid proof of work")
        total = parent.total_work + block.work
        self.entries[block.hash] = ChainEntry(block, total)    # LUÔN lưu, kể cả khi thua
        if total > self.total_work:                  # nặng hơn HẲN mới đổi tip (bằng thì không)
            depth = self._reorg_depth(block)
            if depth > 0:
                self.reorg_count = self.reorg_count + 1
                self.reorg_depths.append(depth)
            self.tip_hash = block.hash
            return True
        return False

    def _reorg_depth(self, new_tip: Block) -> int:
        """How many blocks of the current main chain get dropped (0 = simple extension)."""
        if new_tip.prev_hash == self.tip_hash:       # nối tiếp tip -> không bỏ block nào
            return 0
        old_chain = set(b.hash for b in self.canonical())
        cursor = new_tip.prev_hash
        while cursor not in old_chain:               # lùi đến điểm rẽ nhánh
            cursor = self.entries[cursor].block.prev_hash
        fork_height = self.entries[cursor].block.height
        return self.height - fork_height             # số block bị bỏ

    def replay_state(self, initial, reward: int = 50) -> State:
        """Rebuild all balances by replaying the main chain from the start."""
        state = State(initial)
        for block in self.canonical()[1:]:           # bỏ qua genesis
            state.apply_block(block, reward=reward)
        return state

    def block_intervals(self) -> list:
        blocks = self.canonical()
        return [b.timestamp - a.timestamp for a, b in zip(blocks, blocks[1:])]


def make_block(chain: Chain, *, txs=(), difficulty=None, miner="", timestamp=None) -> Block:
    """Build an unmined block on top of the current tip."""
    tip = chain.tip
    if difficulty is None:
        difficulty = tip.difficulty
    if timestamp is None:
        timestamp = time.time()
    return Block(height=tip.height + 1, prev_hash=tip.hash, timestamp=timestamp,
                 txs=txs, difficulty=difficulty, miner=miner)