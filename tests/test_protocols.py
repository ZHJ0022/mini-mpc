"""通用线性协议与单进程分类对照。 / Linear protocols and classification references."""

import pytest

from mini_mpc.math.field import FieldElement
from mini_mpc.protocol.reference import (
    classify_threshold_level_shares,
    secure_sum,
    secure_sum_shares,
    secure_weighted_sum,
    secure_weighted_sum_shares,
)
from mini_mpc.sharing.share import Share
from mini_mpc.sharing.shamir import reconstruct_secret


def test_secure_sum_reconstructs_sum_of_private_values():
    result = secure_sum([1, 2, 3, 4], threshold=2, num_parties=3, modulus=17)

    assert result == FieldElement(10, 17)


def test_secure_sum_shares_return_reconstructable_output_shares():
    output_shares = secure_sum_shares(
        [1, 2, 3, 4], threshold=2, num_parties=3, modulus=17
    )

    assert len(output_shares) == 3
    assert all(isinstance(share, Share) for share in output_shares)
    assert reconstruct_secret(output_shares[:2]) == FieldElement(10, 17)


def test_secure_sum_wraps_result_in_field():
    result = secure_sum([8, 9, 10], threshold=2, num_parties=3, modulus=17)

    assert result == FieldElement(10, 17)


def test_secure_sum_accepts_threshold_subset_for_reconstruction():
    result = secure_sum([5, 6, 7], threshold=3, num_parties=5, modulus=31)

    assert result == FieldElement(18, 31)


def test_secure_sum_rejects_empty_values():
    with pytest.raises(ValueError):
        secure_sum([], threshold=2, num_parties=3, modulus=17)


def test_secure_sum_rejects_invalid_sharing_parameters():
    with pytest.raises(ValueError):
        secure_sum([1, 2], threshold=0, num_parties=3, modulus=17)

    with pytest.raises(ValueError):
        secure_sum([1, 2], threshold=2, num_parties=5, modulus=5)


def test_secure_weighted_sum_reconstructs_public_weighted_sum():
    result = secure_weighted_sum(
        [3, 4, 5],
        [6, 4, 3],
        threshold=2,
        num_parties=3,
        modulus=101,
    )

    assert result == FieldElement(49, 101)


def test_secure_weighted_sum_shares_return_reconstructable_output_shares():
    output_shares = secure_weighted_sum_shares(
        [3, 4, 5],
        [6, 4, 3],
        threshold=2,
        num_parties=3,
        modulus=101,
    )

    assert len(output_shares) == 3
    assert all(isinstance(share, Share) for share in output_shares)
    assert reconstruct_secret(output_shares[:2]) == FieldElement(49, 101)


def test_secure_weighted_sum_supports_zero_and_negative_weights():
    result = secure_weighted_sum(
        [7, 8, 9],
        [0, -2, 3],
        threshold=2,
        num_parties=3,
        modulus=101,
    )

    assert result == FieldElement(11, 101)


def test_secure_weighted_sum_rejects_empty_values():
    with pytest.raises(ValueError):
        secure_weighted_sum([], [], threshold=2, num_parties=3, modulus=17)


def test_secure_weighted_sum_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        secure_weighted_sum([1, 2], [3], threshold=2, num_parties=3, modulus=17)


def test_secure_weighted_sum_rejects_non_integer_weights():
    with pytest.raises(TypeError):
        secure_weighted_sum([1, 2], [3, 1.5], threshold=2, num_parties=3, modulus=17)


def test_classify_threshold_level_shares_returns_level_code_shares():
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

    assert len(level_shares) == 3
    assert reconstruct_secret(level_shares[:2]) == FieldElement(2, 251)


def test_classify_threshold_level_shares_rejects_invalid_configuration():
    score_shares = secure_sum_shares([140], threshold=2, num_parties=3, modulus=251)

    with pytest.raises(ValueError):
        classify_threshold_level_shares(
            score_shares,
            thresholds={"high": 80},
            level_codes={"none": 0},
            score_range=(0, 130),
            threshold=2,
            num_parties=3,
            modulus=251,
        )

    with pytest.raises(ValueError):
        classify_threshold_level_shares(
            score_shares,
            thresholds={"high": 80},
            level_codes={"none": 0, "high": 3},
            score_range=(0, 130),
            threshold=2,
            num_parties=3,
            modulus=251,
        )


@pytest.mark.parametrize("values,weights", [
    ([True], [1]), ([1.5], [1]), ([1], [True]), ([1], [1.5]),
])
def test_linear_protocol_rejects_non_integer_inputs(values, weights):
    with pytest.raises(TypeError, match="integers"):
        secure_weighted_sum(values, weights, threshold=2, num_parties=3, modulus=17)
