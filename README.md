# Lab 03 – Consensus Simulation (my own implementation)

Course: Introduction to Blockchain Programming – Óbuda University
Student: Jenny (JIGJDS)

## What is inside
| File | What it does |
|---|---|
| `consensus/chain.py` | Account ledger with nonces, blocks, heaviest-chain fork choice, reorg depth |
| `consensus/pow.py` | Target/difficulty, clamped retarget, multi-process miner with a shared stop flag, block-interval statistics |
| `consensus/pos.py` | Casper FFG: justification (> 2/3), finality, double/surround vote slashing, correlated penalty, inactivity leak |
| `consensus/network.py` | Async gossip network: dedup filter, partition and heal, head/getchain/chain reconciliation |
| `tests/test_consensus.py` | The teacher's test suite (unchanged) |
| `tests/test_my_*.py` | My own extra tests |

## How to run
    uv sync
    uv run pytest                          # 51 passed
    uv run python -m consensus.network 1   # partition demo

## Results
- All 51 tests pass (42 teacher tests + 9 of my own).
- Partition demo (seed 1): split into 2 branches (heights 19 and 27); after heal, every node is on 1 chain (height 27). The lighter side had a reorg of depth 5.

## One improvement over the reference code
The README asks that transactions from orphaned blocks go back to the mempool.
My `_reconcile_mempool` does this, and `test_orphaned_transaction_returns_to_the_mempool` proves it.