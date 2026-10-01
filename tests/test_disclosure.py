"""接收者身份及重构份额数量。 / Receiver identity and reconstruction share counts."""

import pytest

from mini_mpc.math.field import FieldElement
from mini_mpc.protocol.reference import (
    classify_threshold_level_shares,
    secure_weighted_sum_shares,
)
from mini_mpc.runtime.disclosure import OutputDisclosurePolicy, reveal_to_receiver
from mini_mpc.sharing.shamir import reconstruct_secret


def test_disclosure_policy_records_receiver_and_min_shares():
    policy = OutputDisclosurePolicy(receiver_id="insurer", min_shares=2)

    assert policy.receiver_id == "insurer"
    assert policy.min_shares == 2


def test_disclosure_policy_rejects_invalid_metadata():
    with pytest.raises(ValueError):
        OutputDisclosurePolicy(receiver_id="", min_shares=2)

    with pytest.raises(ValueError):
        OutputDisclosurePolicy(receiver_id="insurer", min_shares=0)


def test_reveal_to_receiver_allows_authorized_receiver():
    output_shares = secure_weighted_sum_shares(
        [3, 4, 5],
        [6, 4, 3],
        threshold=2,
        num_parties=3,
        modulus=101,
    )
    policy = OutputDisclosurePolicy(receiver_id="insurer", min_shares=2)

    result = reveal_to_receiver(output_shares, policy, receiver_id="insurer")

    assert result == FieldElement(49, 101)


def test_reveal_to_receiver_rejects_unauthorized_receiver():
    output_shares = secure_weighted_sum_shares(
        [3, 4, 5],
        [6, 4, 3],
        threshold=2,
        num_parties=3,
        modulus=101,
    )
    policy = OutputDisclosurePolicy(receiver_id="insurer", min_shares=2)

    with pytest.raises(PermissionError):
        reveal_to_receiver(output_shares, policy, receiver_id="telecom")


def test_reveal_to_receiver_rejects_too_few_output_shares():
    output_shares = secure_weighted_sum_shares(
        [3, 4, 5],
        [6, 4, 3],
        threshold=2,
        num_parties=3,
        modulus=101,
    )
    policy = OutputDisclosurePolicy(receiver_id="insurer", min_shares=2)

    with pytest.raises(ValueError):
        reveal_to_receiver(output_shares[:1], policy, receiver_id="insurer")


def test_single_level_share_cannot_reconstruct_output():
    score_shares = secure_weighted_sum_shares(
        [5, 3, 6],
        [6, 4, 3],
        threshold=2,
        num_parties=3,
        modulus=251,
    )
    level_shares = classify_threshold_level_shares(
        score_shares,
        thresholds={"high": 80, "medium": 45, "low": 20},
        level_codes={"none": 0, "low": 1, "medium": 2, "high": 3},
        score_range=(0, 130),
        threshold=2,
        num_parties=3,
        modulus=251,
    )

    with pytest.raises(ValueError):
        reconstruct_secret(level_shares[:1])
