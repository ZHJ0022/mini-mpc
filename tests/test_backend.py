"""内存后端的共享计算与接收者约束。 / Shared computation and receiver checks in memory."""

import pytest

from mini_mpc.math.field import FieldElement
from mini_mpc.protocol.reference import secure_weighted_sum_shares
from mini_mpc.runtime.backend import InMemoryShamirBackend
from mini_mpc.runtime.disclosure import OutputDisclosurePolicy
from mini_mpc.sharing.shamir import reconstruct_secret


def test_in_memory_backend_weighted_sum_matches_reference_protocol():
    # 测试 backend 线性求和是否等价于 reference 协议。
    # Test backend linear summation against the reference protocol.
    backend = InMemoryShamirBackend()
    output_shares = backend.weighted_sum_shares(
        [5, 3, 6],
        [6, 4, 3],
        threshold=2,
        num_parties=3,
        modulus=251,
        session_id="audit-001",
    )
    reference_shares = secure_weighted_sum_shares(
        [5, 3, 6],
        [6, 4, 3],
        threshold=2,
        num_parties=3,
        modulus=251,
    )

    assert reconstruct_secret(output_shares[:2]) == reconstruct_secret(
        reference_shares[:2]
    )
    assert "audit-001" in backend.sessions


def test_in_memory_backend_classifies_and_discloses_level_code():
    backend = InMemoryShamirBackend()
    score_shares = backend.weighted_sum_shares(
        [5, 3, 6],
        [6, 4, 3],
        threshold=2,
        num_parties=3,
        modulus=251,
    )
    level_shares = backend.classify_level_shares(
        score_shares,
        thresholds={"high": 80, "medium": 45, "low": 20},
        level_codes={"none": 0, "low": 1, "medium": 2, "high": 3},
        score_range=(0, 130),
        threshold=2,
        num_parties=3,
        modulus=251,
    )

    disclosed = backend.disclose_to_receiver(
        level_shares,
        OutputDisclosurePolicy(receiver_id="insurer", min_shares=2),
        "insurer",
    )

    assert disclosed == FieldElement(2, 251)


@pytest.mark.parametrize(
    ("score", "expected_code"),
    [
        (0, 0),
        (19, 0),
        (20, 1),
        (44, 1),
        (45, 2),
        (79, 2),
        (80, 3),
        (130, 3),
    ],
)
def test_in_memory_backend_classifies_level_boundaries(score, expected_code):
    backend = InMemoryShamirBackend()
    score_shares = backend.weighted_sum_shares(
        [score],
        [1],
        threshold=2,
        num_parties=3,
        modulus=251,
        session_id=f"score-{score}",
    )

    level_shares = backend.classify_level_shares(
        score_shares,
        thresholds={"high": 80, "medium": 45, "low": 20},
        level_codes={"none": 0, "low": 1, "medium": 2, "high": 3},
        score_range=(0, 130),
        threshold=2,
        num_parties=3,
        modulus=251,
    )

    assert reconstruct_secret(level_shares[:2]) == FieldElement(expected_code, 251)


def test_in_memory_backend_level_share_is_not_reconstructable_alone():
    backend = InMemoryShamirBackend()
    score_shares = backend.weighted_sum_shares(
        [60],
        [1],
        threshold=2,
        num_parties=3,
        modulus=251,
    )

    level_shares = backend.classify_level_shares(
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


def test_in_memory_backend_rejects_incomplete_score_shares():
    backend = InMemoryShamirBackend()
    score_shares = backend.weighted_sum_shares(
        [60],
        [1],
        threshold=2,
        num_parties=3,
        modulus=251,
    )

    with pytest.raises(ValueError):
        backend.classify_level_shares(
            score_shares[:2],
            thresholds={"high": 80, "medium": 45, "low": 20},
            level_codes={"none": 0, "low": 1, "medium": 2, "high": 3},
            score_range=(0, 130),
            threshold=2,
            num_parties=3,
            modulus=251,
        )


def test_in_memory_backend_rejects_invalid_linear_inputs():
    backend = InMemoryShamirBackend()

    with pytest.raises(ValueError):
        backend.weighted_sum_shares([], [], threshold=2, num_parties=3, modulus=251)
    with pytest.raises(ValueError):
        backend.weighted_sum_shares([1], [1, 2], threshold=2, num_parties=3, modulus=251)
    with pytest.raises(TypeError):
        backend.weighted_sum_shares([1.5], [1], threshold=2, num_parties=3, modulus=251)
