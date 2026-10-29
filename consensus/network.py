"""Asynchronous gossip network with partitions and reconciliation -- my own implementation."""
import asyncio
import random
from dataclasses import dataclass, field

from .chain import Chain, genesis_block, make_block


@dataclass
class Message:
    """kind: "tx", "block", "head", "getchain" or "chain"."""
    kind: str
    payload: object
    sender: str


class Network:
    """Carries messages between nodes, with a small random delay, unless a partition blocks them."""

    def __init__(self, latency: float = 0.01, seed: int = 0):
        self.nodes = {}
        self.latency = latency
        self.rng = random.Random(seed)
        self.partitions = None           # None = mọi node nối với nhau; nếu không: danh sách các nhóm
        self.delivered = 0
        self.dropped = 0

    def register(self, node) -> None:
        self.nodes[node.node_id] = node
        node.network = self

    def can_reach(self, a: str, b: str) -> bool:
        """Two nodes can talk only if they are in the same group."""
        if self.partitions is None:
            return True
        for group in self.partitions:
            if a in group and b in group:
                return True
        return False

    def partition(self, groups) -> None:
        self.partitions = groups

    def heal(self) -> None:
        self.partitions = None

    async def deliver(self, source: str, target: str, message: Message) -> None:
        if not self.can_reach(source, target):
            self.dropped = self.dropped + 1             # bị cắt: tin nhắn biến mất trong im lặng
            return
        await asyncio.sleep(self.latency * (0.5 + self.rng.random()))   # độ trễ mạng
        node = self.nodes.get(target)
        if node is not None:
            self.delivered = self.delivered + 1
            await node.inbox.put(message)                # bỏ vào hộp thư của node nhận


@dataclass
class NodeStats:
    blocks_mined: int = 0
    blocks_received: int = 0
    txs_received: int = 0
    txs_rejected: int = 0
    duplicates_suppressed: int = 0
    reorgs: int = 0


class Node:
    """A full node: its own chain copy, a mempool, a 'seen' filter, an inbox, and maybe a miner."""

    def __init__(self, node_id, *, initial_balances, genesis=None, block_reward=50, hash_power=1.0):
        self.node_id = node_id
        self.network = None
        self.peers = []                                  # hàng xóm trực tiếp
        self.chain = Chain(genesis or genesis_block())
        self.initial_balances = dict(initial_balances)
        self.block_reward = block_reward
        self.hash_power = hash_power
        self.mempool = {}                                # giao dịch đang chờ: txid -> tx
        self.seen = set()                                # txid đã gặp (chống lặp vô tận)
        self.inbox = asyncio.Queue()                     # hộp thư
        self.stats = NodeStats()
        self.running = False                             # vòng đọc thư đang chạy?
        self.mining = False                              # đang đào?
        self.rng = random.Random(node_id)

    @property
    def tip_hash(self) -> str:
        return self.chain.tip_hash

    @property
    def height(self) -> int:
        return self.chain.height

    def state(self):
        return self.chain.replay_state(self.initial_balances, reward=self.block_reward)

    async def broadcast(self, message: Message, exclude=None) -> None:
        """Send to every peer except `exclude` (the one we heard it from)."""
        sends = [self.network.deliver(self.node_id, peer, message)
                 for peer in self.peers if peer != exclude]
        await asyncio.gather(*sends)                     # gửi cho tất cả cùng lúc

    async def submit_transaction(self, tx) -> bool:
        """A user hands a transaction to this node; if valid, gossip it."""
        if not self._accept_transaction(tx):
            return False
        await self.broadcast(Message("tx", tx, self.node_id))
        return True

    def _accept_transaction(self, tx) -> bool:
        if tx.txid in self.seen:                         # đã gặp rồi -> bỏ qua, không chuyển tiếp
            self.stats.duplicates_suppressed = self.stats.duplicates_suppressed + 1
            return False
        self.seen.add(tx.txid)
        self.stats.txs_received = self.stats.txs_received + 1
        if self.state().validate(tx) is not None:        # không hợp lệ -> không nhận, không chuyển
            self.stats.txs_rejected = self.stats.txs_rejected + 1
            return False
        self.mempool[tx.txid] = tx
        return True

    async def handle(self, message: Message) -> None:
        """React to one message from the inbox."""
        if message.kind == "tx":
            if self._accept_transaction(message.payload):
                await self.broadcast(message, exclude=message.sender)
        elif message.kind == "block":
            await self._handle_block(message)
        elif message.kind == "head":                     # hàng xóm khoe đầu chuỗi của họ
            if not self.chain.has(message.payload.hash):  # mình chưa có -> xin cả chuỗi
                await self.network.deliver(self.node_id, message.sender,
                                           Message("getchain", None, self.node_id))
        elif message.kind == "getchain":                 # ai đó xin chuỗi -> gửi chuỗi chính
            blocks = self.chain.canonical()[1:]
            await self.network.deliver(self.node_id, message.sender,
                                       Message("chain", blocks, self.node_id))
        elif message.kind == "chain":
            self._absorb_chain(message.payload)

    async def _handle_block(self, message: Message) -> None:
        block = message.payload
        if self.chain.has(block.hash):                   # trùng -> bỏ qua
            self.stats.duplicates_suppressed = self.stats.duplicates_suppressed + 1
            return
        before = self.chain.reorg_count
        old_chain = self.chain.canonical()
        try:
            moved = self.chain.add_block(block, check_pow=False)
        except KeyError:                                 # không biết block cha -> xin cả chuỗi
            await self.network.deliver(self.node_id, message.sender,
                                       Message("getchain", None, self.node_id))
            return
        self.stats.blocks_received = self.stats.blocks_received + 1
        self.stats.reorgs = self.stats.reorgs + (self.chain.reorg_count - before)
        if moved:
            self._reconcile_mempool(old_chain)
        await self.broadcast(message, exclude=message.sender)

    def _absorb_chain(self, blocks) -> None:
        """Take a peer's chain; the heavier branch wins (this is what heals a partition)."""
        before = self.chain.reorg_count
        old_chain = self.chain.canonical()
        for block in blocks:
            if self.chain.has(block.hash):
                continue
            try:
                self.chain.add_block(block, check_pow=False)
            except (KeyError, ValueError):
                continue
        self.stats.reorgs = self.stats.reorgs + (self.chain.reorg_count - before)
        self._reconcile_mempool(old_chain)

    def _reconcile_mempool(self, old_chain) -> None:
        """After the tip moves: txs from blocks that left the main chain go back to the mempool."""
        new_hashes = {b.hash for b in self.chain.canonical()}
        for block in old_chain:
            if block.hash not in new_hashes:             # block này bị bỏ (reorg)
                for tx in block.txs:
                    self.mempool[tx.txid] = tx           # giao dịch của nó quay lại hàng chờ
        self._prune_mempool()

    def _prune_mempool(self) -> None:
        """Remove txs that are already in the main chain."""
        in_chain = set()
        for block in self.chain.canonical():
            for tx in block.txs:
                in_chain.add(tx.txid)
        for txid in list(self.mempool):
            if txid in in_chain:
                del self.mempool[txid]

    def _select_transactions(self, limit: int = 4):
        """Pick up to `limit` txs that are valid one after another, highest fee first."""
        state = self.state()
        chosen = []
        for tx in sorted(self.mempool.values(), key=lambda t: (-t.fee, t.nonce)):
            if len(chosen) >= limit:
                break
            if state.validate(tx) is None:
                state.apply(tx)
                chosen.append(tx)
        return tuple(chosen)

    async def mine_forever(self, mean_block_time: float) -> None:
        """Mining modelled as an exponential wait (real hashing is in pow.py)."""
        self.mining = True
        while self.mining:
            await asyncio.sleep(self.rng.expovariate(self.hash_power / mean_block_time))
            if not self.mining:
                return
            block = make_block(self.chain, txs=self._select_transactions(), miner=self.node_id,
                               timestamp=asyncio.get_running_loop().time())
            self.chain.add_block(block, check_pow=False)
            self.stats.blocks_mined = self.stats.blocks_mined + 1
            self._prune_mempool()
            await self.broadcast(Message("block", block, self.node_id))

    async def announce_head(self) -> None:
        """Tell the peers our tip, so nodes that were cut off can catch up."""
        await self.broadcast(Message("head", self.chain.tip, self.node_id))

    async def run(self) -> None:
        """Read the inbox forever (checking every 0.1 s whether we should stop)."""
        self.running = True
        while self.running:
            try:
                message = await asyncio.wait_for(self.inbox.get(), timeout=0.1)
            except TimeoutError:
                continue
            await self.handle(message)

    def stop(self) -> None:
        self.running = False
        self.mining = False


def build_network(node_count=8, *, peers_per_node=3, latency=0.01, seed=7, initial_balances=None):
    """A ring (so everyone is reachable) plus random extra links. Needs node_count > peers_per_node."""
    rng = random.Random(seed)
    if initial_balances is None:
        initial_balances = {f"user{i}": 1000 for i in range(4)}
    genesis = genesis_block()
    network = Network(latency=latency, seed=seed)
    nodes = {}
    for i in range(node_count):
        node = Node(f"node{i}", initial_balances=initial_balances, genesis=genesis)
        nodes[node.node_id] = node
        network.register(node)
    ids = list(nodes)
    for i in range(len(ids)):                            # nối thành vòng tròn
        a, b = ids[i], ids[(i + 1) % len(ids)]
        nodes[a].peers.append(b)
        nodes[b].peers.append(a)
    for nid in ids:                                      # thêm cạnh ngẫu nhiên
        while len(nodes[nid].peers) < peers_per_node:
            other = rng.choice(ids)
            if other != nid and other not in nodes[nid].peers:
                nodes[nid].peers.append(other)
                nodes[other].peers.append(nid)
    for node in nodes.values():
        node.peers = sorted(set(node.peers))
    return network, nodes


@dataclass
class SimulationResult:
    nodes: dict
    network: Network
    events: list = field(default_factory=list)

    def converged(self) -> bool:
        return len({n.tip_hash for n in self.nodes.values()}) == 1

    def heights(self) -> list:
        return sorted({n.height for n in self.nodes.values()})


async def simulate_partition(node_count=12, mean_block_time=0.25, seed=1):
    """Part C with my own code: connected -> split in half -> heal (mining stops at heal)."""
    network, nodes = build_network(node_count=node_count, latency=0.005, seed=seed)
    result = SimulationResult(nodes, network)
    tasks = [asyncio.create_task(n.run()) for n in nodes.values()]
    ids = list(nodes)
    miners = ids[::max(1, node_count // 3)]              # thợ đào rải đều 2 phía
    for nid in miners:
        tasks.append(asyncio.create_task(nodes[nid].mine_forever(mean_block_time)))

    async def announcer():
        while True:
            await asyncio.sleep(0.15)
            for n in nodes.values():
                await n.announce_head()

    tasks.append(asyncio.create_task(announcer()))
    await asyncio.sleep(1.6)
    result.events.append(f"phase 1 (connected)  heights={result.heights()} converged={result.converged()}")
    half = node_count // 2
    network.partition([set(ids[:half]), set(ids[half:])])
    result.events.append(f"PARTITION  A={ids[:half]}  B={ids[half:]}")
    await asyncio.sleep(1.5)
    tips = len({n.tip_hash for n in nodes.values()})
    result.events.append(f"phase 2 (split)      distinct tips={tips} heights={result.heights()}")
    network.heal()
    result.events.append("HEAL")
    for nid in miners:
        nodes[nid].mining = False                        # dừng đào để chụp kết quả cuối cho rõ
    await asyncio.sleep(1.5)
    tips = len({n.tip_hash for n in nodes.values()})
    result.events.append(f"phase 3 (healed, mining stopped) distinct tips={tips} "
                         f"heights={result.heights()} converged={result.converged()}")
    for n in nodes.values():
        n.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    return result


if __name__ == "__main__":
    import sys
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    res = asyncio.run(simulate_partition(seed=seed))
    for e in res.events:
        print(e)
    for nid, node in res.nodes.items():
        print(f"  {nid:<7} height={node.height}  reorg depths={node.chain.reorg_depths}")