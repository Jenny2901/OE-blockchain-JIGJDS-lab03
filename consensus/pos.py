"""Casper FFG proof of stake -- my own implementation (written in lesson 3)."""

MAX_EFFECTIVE_BALANCE = None


class Checkpoint: pass
class Attestation: pass
class BeaconState: pass
def is_double_vote(a, b): raise NotImplementedError
def is_surround_vote(a, b): raise NotImplementedError
def find_violations(attestations): raise NotImplementedError
def honest_attestations(state, source, target, validators=None): raise NotImplementedError
