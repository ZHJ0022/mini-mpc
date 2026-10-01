"""份额元数据及同一参与方的本地运算。 / Share metadata and party-local operations."""

import pytest

from mini_mpc.math.field import FieldElement
from mini_mpc.sharing.share import Share


def test_share_records_metadata():
    share = Share(
        party_id=1,
        value=FieldElement(5, 17),
        threshold=3,
        num_parties=5,
    )

    assert share.party_id == 1
    assert share.value == FieldElement(5, 17)
    assert share.threshold == 3
    assert share.num_parties == 5


def test_share_rejects_non_field_value():
    with pytest.raises(TypeError):
        Share(party_id=1, value=5, threshold=2, num_parties=3)


def test_share_rejects_invalid_party_id():
    with pytest.raises(ValueError):
        Share(
            party_id=0,
            value=FieldElement(5, 17),
            threshold=2,
            num_parties=3,
        )

    with pytest.raises(ValueError):
        Share(
            party_id=4,
            value=FieldElement(5, 17),
            threshold=2,
            num_parties=3,
        )


def test_share_rejects_invalid_threshold_and_party_count():
    with pytest.raises(ValueError):
        Share(
            party_id=1,
            value=FieldElement(5, 17),
            threshold=0,
            num_parties=3,
        )

    with pytest.raises(ValueError):
        Share(
            party_id=1,
            value=FieldElement(5, 17),
            threshold=4,
            num_parties=3,
        )


def test_share_adds_compatible_shares():
    left = Share(1, FieldElement(12, 17), threshold=2, num_parties=3)
    right = Share(1, FieldElement(9, 17), threshold=2, num_parties=3)

    result = left + right

    assert result == Share(1, FieldElement(4, 17), threshold=2, num_parties=3)


def test_share_subtracts_compatible_shares():
    left = Share(1, FieldElement(3, 17), threshold=2, num_parties=3)
    right = Share(1, FieldElement(9, 17), threshold=2, num_parties=3)

    result = left - right

    assert result == Share(1, FieldElement(11, 17), threshold=2, num_parties=3)


def test_share_scales_by_public_integer():
    share = Share(1, FieldElement(5, 17), threshold=2, num_parties=3)
    expected = Share(1, FieldElement(15, 17), threshold=2, num_parties=3)

    assert share.scale(3) == expected
    assert share * 3 == expected
    assert 3 * share == expected


def test_share_rejects_non_integer_scalar():
    share = Share(1, FieldElement(5, 17), threshold=2, num_parties=3)

    with pytest.raises(TypeError):
        share.scale(1.5)

    with pytest.raises(TypeError):
        _ = share * 1.5


def test_share_addition_rejects_non_share():
    share = Share(1, FieldElement(5, 17), threshold=2, num_parties=3)

    with pytest.raises(TypeError):
        _ = share + 1


def test_share_operations_reject_different_party_ids():
    left = Share(1, FieldElement(5, 17), threshold=2, num_parties=3)
    right = Share(2, FieldElement(6, 17), threshold=2, num_parties=3)

    with pytest.raises(ValueError):
        _ = left + right


def test_share_operations_reject_different_moduli():
    left = Share(1, FieldElement(5, 17), threshold=2, num_parties=3)
    right = Share(1, FieldElement(6, 19), threshold=2, num_parties=3)

    with pytest.raises(ValueError):
        _ = left + right


def test_share_operations_reject_different_thresholds():
    left = Share(1, FieldElement(5, 17), threshold=2, num_parties=3)
    right = Share(1, FieldElement(6, 17), threshold=3, num_parties=3)

    with pytest.raises(ValueError):
        _ = left - right


def test_share_operations_reject_different_party_counts():
    left = Share(1, FieldElement(5, 17), threshold=2, num_parties=3)
    right = Share(1, FieldElement(6, 17), threshold=2, num_parties=4)

    with pytest.raises(ValueError):
        _ = left - right


@pytest.mark.parametrize("field", ["party_id", "threshold", "num_parties"])
@pytest.mark.parametrize("invalid", [True, 1.5])
def test_share_metadata_rejects_non_integers(field, invalid):
    args = dict(party_id=1, value=FieldElement(3, 17), threshold=2, num_parties=3)
    args[field] = invalid
    with pytest.raises(TypeError, match="integer"):
        Share(**args)


def test_share_scalar_rejects_boolean():
    with pytest.raises(TypeError):
        Share(1, FieldElement(3, 17), 2, 3).scale(True)
