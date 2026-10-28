"""My own tests for requirements 2 and 3 (proof of work and interval statistics)."""
import multiprocessing as mp
import random

from consensus.chain import Chain, make_block
from consensus.pow import MiningStats, mine_parallel, retarget


def test_retarget_matches_the_four_cases_of_part_a():
    assert retarget(1000, 300, 600) == 2000
    assert retarget(1000, 1200, 600) == 500
    assert retarget(1000, 6, 600) == 4000        # clamped at 4x
    assert retarget(1000, 60000, 600) == 250     # clamped at 1/4


def test_parallel_miner_finds_a_valid_nonce_and_all_workers_exit():
    block = make_block(Chain(), miner="m", timestamp=1.0)
    mined = mine_parallel(block, workers=2)
    assert mined.is_valid_pow()
    assert mp.active_children() == []            # no worker process left running


def test_statistics_on_an_exponential_sample():
    rng = random.Random(42)
    sample = [rng.expovariate(1.0) for _ in range(5000)]
    stats = MiningStats(sample, [1])
    assert 0.60 <= stats.median / stats.mean <= 0.80
    assert 0.80 <= stats.stdev / stats.mean <= 1.20