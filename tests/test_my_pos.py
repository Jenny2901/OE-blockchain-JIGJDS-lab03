"""My own tests for requirements 4, 5 and 6 (Casper FFG, slashing, inactivity leak)."""
from consensus.pos import GWEI, Attestation, BeaconState, Checkpoint, honest_attestations


def _loss_with(offenders: int) -> float:
    state = BeaconState(validator_count=64)
    g, a, b = state.genesis_checkpoint, Checkpoint(1, "chain-A"), Checkpoint(1, "chain-B")
    for i in range(64):
        state.attest(Attestation(i, g, a))
    for i in range(offenders):
        state.attest(Attestation(i, g, b))
    state.process_epoch(1)
    return round((32 * GWEI - state.validators[0].balance) / GWEI, 2)


def test_loss_table_matches_part_b():
    assert _loss_with(1) == 2.41
    assert _loss_with(5) == 8.06
    assert _loss_with(10) == 15.15
    assert _loss_with(20) == 29.43
    for k in (22, 25, 30):
        assert _loss_with(k) == 32.0


def test_surround_vote_is_slashed_with_its_rule():
    state = BeaconState(validator_count=16)
    state.attest(Attestation(5, Checkpoint(0, "g"), Checkpoint(6, "far")))
    state.attest(Attestation(5, Checkpoint(2, "b"), Checkpoint(4, "near")))
    result = state.process_epoch(6)
    assert state.validators[5].slashed
    assert result["slashed"][0].rule == "surround_vote"


def test_leak_waits_four_epochs_then_finality_resumes():
    state = BeaconState(validator_count=64)
    present = list(range(40, 64))                      # 24 of 64 online: no quorum
    leaks, finalized_at = [], None
    for epoch in range(1, 101):
        target = Checkpoint(epoch, f"root-{epoch}")
        state.attest_many(honest_attestations(state, state.current_justified, target, present))
        result = state.process_epoch(epoch)
        leaks.append(result["inactivity_leak"])
        if result["finalized"] is not None:
            finalized_at = epoch
            break
    assert leaks[:4] == [0, 0, 0, 0]                   # grace period
    assert all(x > 0 for x in leaks[4:-2])             # then the leak runs
    assert state.validators[40].balance == 32 * GWEI   # participants untouched
    assert state.validators[0].balance < 32 * GWEI     # absentees drained
    assert finalized_at is not None and finalized_at <= 100