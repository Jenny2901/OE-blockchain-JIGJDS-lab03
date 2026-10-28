"""Proof of work: target/difficulty, retargeting, mining (single and parallel), statistics -- my own implementation."""
import multiprocessing as mp
import statistics
import time
from dataclasses import dataclass

from .chain import GENESIS_TARGET, MAX_TARGET, Chain, genesis_block, make_block, sha256d

MAX_ADJUSTMENT_FACTOR = 4        # mỗi lần điều chỉnh thay đổi tối đa 4 lần


def target_from_difficulty(difficulty: int) -> int:
    return GENESIS_TARGET // max(1, difficulty)


def difficulty_from_target(target: int) -> int:
    return GENESIS_TARGET // max(1, target)


def retarget(current_difficulty, actual_seconds, target_seconds, clamp=MAX_ADJUSTMENT_FACTOR):
    """new = old / (actual / expected), with the ratio clamped to [1/4, 4] and a floor of 1."""
    if actual_seconds <= 0:
        actual_seconds = target_seconds / clamp
    ratio = actual_seconds / target_seconds          # < 1: quá nhanh, > 1: quá chậm
    if ratio > clamp:                                # kẹp trên
        ratio = clamp
    if ratio < 1 / clamp:                            # kẹp dưới
        ratio = 1 / clamp
    new_difficulty = int(current_difficulty / ratio)
    return max(1, new_difficulty)                    # không bao giờ dưới 1


def mine_range(block, start, stop, step=1):
    """Try nonces start, start+step, start+2*step, ... Return the winner or None."""
    target = block.target
    for nonce in range(start, stop, step):
        if int.from_bytes(sha256d(block.header_bytes(nonce)), "big") < target:
            return nonce
    return None


def mine(block, max_nonce=2**32):
    """Single-process mining."""
    nonce = mine_range(block, 0, max_nonce)
    if nonce is None:
        raise RuntimeError("nonce space exhausted")
    return block.with_nonce(nonce)


def _worker(block, offset, stride, found, queue):
    """Runs in a child process: strided search, checking the shared flag every 4096 hashes."""
    target = block.target
    nonce = offset                                   # công nhân i bắt đầu từ nonce i
    while not found.value:                           # chưa ai thắng thì đào tiếp
        for _ in range(4096):
            if int.from_bytes(sha256d(block.header_bytes(nonce)), "big") < target:
                found.value = 1                      # giơ cờ: "tôi tìm ra rồi!"
                queue.put(nonce)                     # bỏ nonce vào hộp thư
                return
            nonce = nonce + stride                   # nhảy so le
        if nonce > 2**32:
            return


def mine_parallel(block, workers=None, timeout=60.0):
    """Mine with several processes (not threads: the GIL stops threads hashing in parallel)."""
    if workers is None:
        workers = max(1, mp.cpu_count() - 1)
    if workers == 1:
        return mine(block)
    ctx = mp.get_context("spawn")
    found = ctx.Value("i", 0)                        # cờ dùng chung, ban đầu = 0
    queue = ctx.Queue()                              # hộp thư
    procs = []
    for i in range(workers):
        p = ctx.Process(target=_worker, args=(block, i, workers, found, queue), daemon=True)
        procs.append(p)
        p.start()
    try:
        nonce = queue.get(timeout=timeout)           # chờ người thắng
    finally:                                         # LUÔN dừng hết các tiến trình
        found.value = 1
        for p in procs:
            p.join(timeout=2.0)
            if p.is_alive():
                p.terminate()
                p.join()
    mined = block.with_nonce(nonce)
    if not mined.is_valid_pow():
        raise RuntimeError("worker returned an invalid nonce")
    return mined


@dataclass
class MiningStats:
    """Block intervals and difficulties collected during a simulation."""
    intervals: list
    difficulties: list

    @property
    def mean(self) -> float:
        return statistics.fmean(self.intervals)

    @property
    def median(self) -> float:
        return statistics.median(self.intervals)

    @property
    def stdev(self) -> float:
        return statistics.pstdev(self.intervals)

    def report(self) -> str:
        return "\n".join([
            f"blocks mined        : {len(self.intervals)}",
            f"mean interval       : {self.mean:.3f} s",
            f"median interval     : {self.median:.3f} s",
            f"stdev of intervals  : {self.stdev:.3f} s",
            f"min / max interval  : {min(self.intervals):.3f} / {max(self.intervals):.3f} s",
            f"difficulty start/end: {self.difficulties[0]} -> {self.difficulties[-1]}",
            f"median / mean       : {self.median / self.mean:.3f}   (exponential predicts ~0.693)",
            f"stdev / mean        : {self.stdev / self.mean:.3f}   (exponential predicts ~1.0)",
        ])


def simulate_mining(blocks=30, retarget_interval=10, target_seconds=0.30, workers=1):
    """Mine a chain, retarget every `retarget_interval` blocks, and collect statistics."""
    chain = Chain(genesis_block())
    intervals = []
    difficulties = [1]
    difficulty = 1
    window_start = time.perf_counter()
    for i in range(blocks):
        started = time.perf_counter()
        candidate = make_block(chain, difficulty=difficulty, miner="me", timestamp=started)
        if workers == 1:
            mined = mine(candidate)
        else:
            mined = mine_parallel(candidate, workers=workers)
        intervals.append(time.perf_counter() - started)
        chain.add_block(mined)
        if (i + 1) % retarget_interval == 0:          # hết một cửa sổ -> điều chỉnh độ khó
            window = time.perf_counter() - window_start
            difficulty = retarget(difficulty, window, target_seconds * retarget_interval)
            window_start = time.perf_counter()
        difficulties.append(difficulty)
    return chain, MiningStats(intervals, difficulties)