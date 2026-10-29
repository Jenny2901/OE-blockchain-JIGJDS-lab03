"""Casper FFG proof of stake: finality, slashing, inactivity leak -- my own implementation."""
from dataclasses import dataclass

GWEI = 10**9                              # 1 ETH = 10^9 gwei (số nguyên, tính chính xác)
MAX_EFFECTIVE_BALANCE = 32 * GWEI         # mỗi validator đặt cọc 32 ETH
INITIAL_SLASHING_QUOTIENT = 32            # phạt ban đầu = 1/32 số dư
PROPORTIONAL_SLASHING_MULTIPLIER = 3      # hệ số 3 trong phạt "tập thể"
MIN_EPOCHS_TO_INACTIVITY_PENALTY = 4      # chờ 4 epoch rồi mới bắt đầu rò rỉ
INACTIVITY_PENALTY_QUOTIENT = 64          # mỗi epoch rút 1/64 số dư của người vắng mặt


@dataclass(frozen=True)
class Checkpoint:
    """A voting milestone: an epoch number and a block root."""
    epoch: int
    root: str


@dataclass(frozen=True)
class Attestation:
    """A vote by one validator for the link source -> target."""
    validator: int
    source: Checkpoint
    target: Checkpoint


@dataclass
class Validator:
    """A staker. Slashed or empty validators no longer count."""
    index: int
    balance: int = MAX_EFFECTIVE_BALANCE
    slashed: bool = False

    @property
    def is_active(self) -> bool:
        return not self.slashed and self.balance > 0

    @property
    def effective_balance(self) -> int:
        return min(self.balance, MAX_EFFECTIVE_BALANCE)      # tối đa 32 ETH được tính


def is_double_vote(a: Attestation, b: Attestation) -> bool:
    """Same validator, same target epoch, different target root."""
    return (a.validator == b.validator
            and a.target.epoch == b.target.epoch
            and a.target.root != b.target.root)


def is_surround_vote(a: Attestation, b: Attestation) -> bool:
    """Vote a's span strictly encloses vote b's span (same validator)."""
    if a.validator != b.validator:
        return False
    return a.source.epoch < b.source.epoch and b.target.epoch < a.target.epoch


def find_violations(attestations):
    """Check every PAIR of votes; return (vote, vote, rule) for each slashable pair."""
    found = []
    n = len(attestations)
    for i in range(n):
        for j in range(i + 1, n):                          # mỗi cặp xét đúng 1 lần
            a, b = attestations[i], attestations[j]
            if is_double_vote(a, b):
                found.append((a, b, "double_vote"))
            elif is_surround_vote(a, b) or is_surround_vote(b, a):
                found.append((a, b, "surround_vote"))
    return found


def honest_attestations(state, source, target, validators=None):
    """Helper: every listed validator (default: all active ones) votes source -> target."""
    if validators is None:
        validators = [v.index for v in state.validators if v.is_active]
    return [Attestation(i, source, target) for i in validators]


# --- written in lesson 3b ---
class BeaconState: pass