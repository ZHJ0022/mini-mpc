"""门限重构及分享参数校验。 / Threshold reconstruction and sharing parameters."""

import pytest

from mini_mpc.math.field import FieldElement
from mini_mpc.sharing.share import Share
from mini_mpc.sharing.shamir import reconstruct_secret, share_secret


def test_share_secret_returns_typed_shares():
    shares = share_secret(secret=5, threshold=3, num_parties=5, modulus=17)

    assert len(shares) == 5
    assert all(isinstance(share, Share) for share in shares)
    assert [share.party_id for share in shares] == [1, 2, 3, 4, 5]
    assert all(share.threshold == 3 for share in shares)
    assert all(share.num_parties == 5 for share in shares)
    assert all(share.value.modulus == 17 for share in shares)


def test_reconstructs_secret_from_threshold_shares():
    shares = share_secret(secret=5, threshold=3, num_parties=5, modulus=17)

    secret = reconstruct_secret(shares[:3])

    assert secret == FieldElement(5, 17)


def test_reconstructs_secret_from_different_valid_subset():
    shares = share_secret(secret=9, threshold=3, num_parties=5, modulus=17)

    secret = reconstruct_secret([shares[0], shares[2], shares[4]])

    assert secret == FieldElement(9, 17)


def test_reconstructs_secret_from_all_shares():
    shares = share_secret(secret=12, threshold=2, num_parties=4, modulus=17)

    secret = reconstruct_secret(shares)

    assert secret == FieldElement(12, 17)


def test_share_secret_rejects_invalid_parameters():
    with pytest.raises(ValueError):
        share_secret(secret=1, threshold=0, num_parties=3, modulus=17)

    with pytest.raises(ValueError):
        share_secret(secret=1, threshold=4, num_parties=3, modulus=17)

    with pytest.raises(ValueError):
        share_secret(secret=1, threshold=2, num_parties=5, modulus=5)

    with pytest.raises(ValueError):
        share_secret(secret=1, threshold=2, num_parties=3, modulus=9)


def test_reconstruct_rejects_empty_shares():
    with pytest.raises(ValueError):
        reconstruct_secret([])


def test_reconstruct_rejects_duplicate_party_ids():
    shares = [
        Share(1, FieldElement(3, 17), threshold=2, num_parties=3),
        Share(1, FieldElement(4, 17), threshold=2, num_parties=3),
    ]

    with pytest.raises(ValueError):
        reconstruct_secret(shares)


def test_reconstruct_rejects_too_few_shares():
    shares = share_secret(secret=5, threshold=3, num_parties=5, modulus=17)

    with pytest.raises(ValueError):
        reconstruct_secret(shares[:2])


def test_reconstruct_rejects_incompatible_fields():
    shares = [
        Share(1, FieldElement(3, 17), threshold=2, num_parties=3),
        Share(2, FieldElement(4, 19), threshold=2, num_parties=3),
    ]

    with pytest.raises(ValueError):
        reconstruct_secret(shares)


def test_reconstruct_rejects_incompatible_thresholds():
    shares = [
        Share(1, FieldElement(3, 17), threshold=2, num_parties=3),
        Share(2, FieldElement(4, 17), threshold=3, num_parties=3),
        Share(3, FieldElement(5, 17), threshold=3, num_parties=3),
    ]

    with pytest.raises(ValueError):
        reconstruct_secret(shares)


def test_reconstruct_rejects_incompatible_party_counts():
    shares = [
        Share(1, FieldElement(3, 17), threshold=2, num_parties=3),
        Share(2, FieldElement(4, 17), threshold=2, num_parties=4),
    ]

    with pytest.raises(ValueError):
        reconstruct_secret(shares)


def test_reconstruct_rejects_non_share_value():
    with pytest.raises(TypeError):
        reconstruct_secret([object()])

    with pytest.raises(TypeError):
        reconstruct_secret([(1, FieldElement(3, 17))])


@pytest.mark.parametrize("field", ["secret", "threshold", "num_parties", "modulus"])
@pytest.mark.parametrize("invalid", [True, 1.5])
def test_sharing_rejects_non_integer_inputs(field, invalid):
    args = dict(secret=5, threshold=2, num_parties=3, modulus=17)
    args[field] = invalid
    with pytest.raises(TypeError, match="integer"):
        share_secret(**args)
