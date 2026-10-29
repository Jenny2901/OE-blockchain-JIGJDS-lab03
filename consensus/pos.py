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


@dataclass
class SlashingRecord:
    """Who was slashed, when, for which rule, and how much was taken."""
    validator: int
    epoch: int
    rule: str
    initial_penalty: int
    correlated_penalty: int = 0


class BeaconState:
    """A small Casper FFG state machine: votes in, justification / finality / penalties out."""

    def __init__(self, validator_count: int, genesis_root: str = "genesis"):
        self.validators = [Validator(i) for i in range(validator_count)]
        self.genesis_checkpoint = Checkpoint(0, genesis_root)
        self.justified = {self.genesis_checkpoint}        # tập các mốc đã justify (genesis có sẵn)
        self.current_justified = self.genesis_checkpoint  # mốc justify mới nhất
        self.finalized = self.genesis_checkpoint
        self.last_finalized_epoch = 0
        self.attestation_log = []                         # mọi lá phiếu đã nhận
        self.slashings = []
        self.slashed_balance_total = 0                    # tổng tiền của những người đã bị phạt

    @property
    def total_active_balance(self) -> int:
        return sum(v.effective_balance for v in self.validators if v.is_active)

    def supermajority_threshold(self) -> int:
        """2/3 of active stake. A link must get STRICTLY MORE than this."""
        return (2 * self.total_active_balance) // 3

    def attest(self, attestation: Attestation) -> None:
        if self.validators[attestation.validator].is_active:   # người đã bị phạt thì không được bầu
            self.attestation_log.append(attestation)

    def attest_many(self, attestations) -> None:
        for a in attestations:
            self.attest(a)

    def process_epoch(self, epoch: int) -> dict:
        """End of an epoch. Order matters: slash FIRST, so a cheater's vote never counts."""
        result = {"epoch": epoch}
        result["slashed"] = self._process_slashings(epoch)
        result["justified"] = self._process_justification(epoch)
        result["finalized"] = self._process_finalization(epoch)
        result["inactivity_leak"] = self._process_inactivity_leak(epoch)
        return result

    def _process_justification(self, epoch: int):
        """Add up stake per link (source -> target) for this epoch; > 2/3 justifies the target."""
        tally = {}
        for a in self.attestation_log:
            if a.target.epoch != epoch:
                continue
            validator = self.validators[a.validator]
            if not validator.is_active:
                continue
            if a.source not in self.justified:           # nguồn chưa justify -> phiếu vô giá trị
                continue
            link = (a.source, a.target)
            tally[link] = tally.get(link, 0) + validator.effective_balance
        threshold = self.supermajority_threshold()
        for (source, target), weight in tally.items():
            if weight > threshold:                       # LỚN HƠN HẲN 2/3, bằng 2/3 không đủ
                self.justified.add(target)
                if target.epoch > self.current_justified.epoch:
                    self.current_justified = target
                return target
        return None

    def _process_finalization(self, epoch: int):
        """A justified checkpoint is finalised when the NEXT epoch's checkpoint is justified too."""
        justified_epochs = {c.epoch for c in self.justified}
        for checkpoint in sorted(self.justified, key=lambda c: c.epoch):
            if checkpoint.epoch + 1 in justified_epochs and checkpoint.epoch > self.finalized.epoch:
                self.finalized = checkpoint
                self.last_finalized_epoch = epoch
                return checkpoint
        return None

    def _process_slashings(self, epoch: int):
        """Find cheaters, take 1/32 at once, then apply the correlated penalty to all of them."""
        records = []
        already = {r.validator for r in self.slashings}  # mỗi người chỉ bị phạt 1 lần
        for a, b, rule in find_violations(self.attestation_log):
            index = a.validator
            if index in already:
                continue
            validator = self.validators[index]
            penalty = validator.effective_balance // INITIAL_SLASHING_QUOTIENT   # phạt ban đầu 1/32
            validator.balance = validator.balance - penalty
            validator.slashed = True
            self.slashed_balance_total = self.slashed_balance_total + validator.effective_balance
            record = SlashingRecord(index, epoch, rule, penalty)
            records.append(record)
            self.slashings.append(record)
            already.add(index)
        if records:
            self._apply_correlated_penalty(records)
        return records

    def _apply_correlated_penalty(self, records) -> None:
        """extra = balance x min(1, 3 x slashed / total): the more cheat together, the more each loses."""
        total = self.total_active_balance + self.slashed_balance_total
        if total == 0:
            return
        fraction = min(1.0, PROPORTIONAL_SLASHING_MULTIPLIER * self.slashed_balance_total / total)
        for record in records:
            validator = self.validators[record.validator]
            extra = int(validator.effective_balance * fraction)
            extra = min(extra, validator.balance)        # không thể mất nhiều hơn số đang có
            validator.balance = validator.balance - extra
            record.correlated_penalty = extra

    def _process_inactivity_leak(self, epoch: int) -> int:
        """After 4 epochs without finality, drain 1/64 per epoch from validators who did NOT vote."""
        if epoch - self.last_finalized_epoch <= MIN_EPOCHS_TO_INACTIVITY_PENALTY:
            return 0                                     # còn trong thời gian chờ
        voted = {a.validator for a in self.attestation_log if a.target.epoch == epoch}
        total_leaked = 0
        for validator in self.validators:
            if not validator.is_active or validator.index in voted:
                continue                                 # người có bỏ phiếu: không mất gì
            penalty = validator.effective_balance // INACTIVITY_PENALTY_QUOTIENT
            penalty = min(penalty, validator.balance)
            validator.balance = validator.balance - penalty
            total_leaked = total_leaked + penalty
        return total_leaked