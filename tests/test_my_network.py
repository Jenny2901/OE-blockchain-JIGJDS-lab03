"""My own tests for requirement 7 (gossip, deduplication, partition, reconciliation)."""
import asyncio

from consensus.chain import Transaction, make_block
from consensus.network import Message, build_network


async def _start(nodes):
    return [asyncio.create_task(n.run()) for n in nodes.values()]


async def _stop(nodes, tasks):
    for n in nodes.values():
        n.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_each_node_processes_a_transaction_exactly_once():
    network, nodes = build_network(node_count=8, latency=0.001, seed=2,
                                   initial_balances={"user0": 100})
    tasks = await _start(nodes)
    try:
        await nodes["node0"].submit_transaction(Transaction("user0", "user1", 5, nonce=0))
        await asyncio.sleep(0.4)
        assert all(n.stats.txs_received == 1 for n in nodes.values())
    finally:
        await _stop(nodes, tasks)


async def test_no_message_crosses_the_partition():
    network, nodes = build_network(node_count=6, latency=0.001, seed=3,
                                   initial_balances={"user0": 100})
    network.partition([{"node0", "node1", "node2"}, {"node3", "node4", "node5"}])
    tasks = await _start(nodes)
    try:
        await nodes["node0"].submit_transaction(Transaction("user0", "user1", 5, nonce=0))
        await asyncio.sleep(0.3)
        assert all(len(nodes[n].mempool) == 1 for n in ("node0", "node1", "node2"))
        assert all(len(nodes[n].mempool) == 0 for n in ("node3", "node4", "node5"))
        assert network.dropped > 0
    finally:
        await _stop(nodes, tasks)


async def test_orphaned_transaction_returns_to_the_mempool():
    network, nodes = build_network(node_count=4, latency=0.001, seed=1,
                                   initial_balances={"user0": 100})
    a, b = nodes["node0"], nodes["node2"]
    network.partition([{"node0", "node1"}, {"node2", "node3"}])
    tasks = await _start(nodes)
    try:
        tx = Transaction("user0", "user1", 5, nonce=0)
        b.mempool[tx.txid] = tx
        orphan = make_block(b.chain, txs=(tx,), miner="node2", timestamp=1.0)
        b.chain.add_block(orphan, check_pow=False)          # B confirms tx in 1 block
        b._prune_mempool()
        assert tx.txid not in b.mempool
        for i in range(2):                                   # A builds a heavier branch
            a.chain.add_block(make_block(a.chain, miner="node0", timestamp=2.0 + i),
                              check_pow=False)
        network.heal()
        await network.deliver("node0", "node2", Message("head", a.chain.tip, "node0"))
        await asyncio.sleep(0.2)
        assert b.tip_hash == a.tip_hash                      # B reorganised onto A's chain
        assert tx.txid in b.mempool                          # and the tx is pending again
    finally:
        await _stop(nodes, tasks)