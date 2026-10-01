"""参与方存储、会话路由及 BGW 运算。 / Party storage, session routing and BGW operations."""

import pytest

from mini_mpc.math.field import FieldElement
from mini_mpc.protocol.reference import secure_weighted_sum_shares
from mini_mpc.runtime.party import MPCParty
from mini_mpc.runtime.session import ProtocolSession
from mini_mpc.sharing.shamir import reconstruct_secret, share_secret
from mini_mpc.sharing.share import Share


def test_party_stores_only_matching_party_shares():
    party = MPCParty(1)
    own_share, other_share = share_secret(
        5,
        threshold=2,
        num_parties=3,
        modulus=251,
    )[:2]

    party.receive_share("session-001", "telco_absence_hours", own_share)

    assert party.get_share("session-001", "telco_absence_hours") == own_share
    with pytest.raises(ValueError):
        party.receive_share("session-001", "other", other_share)


def test_party_store_accepts_identical_retry_and_rejects_conflicting_write():
    # 重试同一份额不会改变状态；同名不同内容不得覆盖已提交输入。
    # An identical retry is safe; conflicting contents cannot overwrite a secret id.
    party = MPCParty(1)
    share = share_secret(5, threshold=2, num_parties=3, modulus=251)[0]
    party.receive_share("session-001", "telco_absence_hours", share)
    party.receive_share("session-001", "telco_absence_hours", share)
    changed = Share(
        party_id=1,
        value=share.value + 1,
        threshold=2,
        num_parties=3,
    )
    with pytest.raises(ValueError, match="conflicting share"):
        party.receive_share("session-001", "telco_absence_hours", changed)
    assert party.get_share("session-001", "telco_absence_hours") == share


def test_protocol_session_routes_each_share_to_matching_party():
    session = ProtocolSession(
        session_id="audit-001",
        threshold=2,
        num_parties=3,
        modulus=251,
    )

    session.submit_secret("clinical_need_score", 6)

    for party_id, party in session.parties.items():
        share = party.get_share("audit-001", "clinical_need_score")
        assert share.party_id == party_id
        assert party.store.keys() == (("audit-001", "clinical_need_score"),)


def test_protocol_session_weighted_sum_matches_reference_protocol():
    values = [5, 3, 6]
    weights = [6, 4, 3]
    session = ProtocolSession(
        session_id="audit-001",
        threshold=2,
        num_parties=3,
        modulus=251,
    )
    for secret_id, value in zip(("a", "b", "c"), values, strict=True):
        session.submit_secret(secret_id, value)

    output_shares = session.compute_weighted_sum(
        ("a", "b", "c"),
        tuple(weights),
        "risk_score",
    )
    reference_shares = secure_weighted_sum_shares(
        values,
        weights,
        threshold=2,
        num_parties=3,
        modulus=251,
    )

    assert reconstruct_secret(output_shares[:2]) == reconstruct_secret(
        reference_shares[:2]
    )
    assert reconstruct_secret(output_shares[:2]) == FieldElement(60, 251)


def test_protocol_session_rejects_reconstruction_with_too_few_shares():
    session = ProtocolSession(
        session_id="audit-001",
        threshold=2,
        num_parties=3,
        modulus=251,
    )
    session.submit_secret("a", 5)
    session.submit_secret("b", 3)

    output_shares = session.compute_weighted_sum(("a", "b"), (6, 4), "risk_score")

    with pytest.raises(ValueError):
        reconstruct_secret(output_shares[:1])


def test_protocol_session_multiplies_shared_values():
    session = ProtocolSession(
        session_id="multiply-001",
        threshold=2,
        num_parties=3,
        modulus=251,
    )
    session.submit_secret("left", 7)
    session.submit_secret("right", 8)

    product_shares = session.multiply_shares("left", "right", "product")

    assert reconstruct_secret(product_shares[:2]) == FieldElement(56, 251)
    with pytest.raises(ValueError):
        reconstruct_secret(product_shares[:1])


def test_protocol_session_rejects_multiplication_without_bgw_bound():
    session = ProtocolSession(
        session_id="multiply-002",
        threshold=3,
        num_parties=4,
        modulus=251,
    )
    session.submit_secret("left", 7)
    session.submit_secret("right", 8)

    with pytest.raises(ValueError):
        session.multiply_shares("left", "right", "product")


def test_protocol_session_evaluates_public_polynomial_on_shared_value():
    session = ProtocolSession(
        session_id="polynomial-001",
        threshold=2,
        num_parties=3,
        modulus=251,
    )
    session.submit_secret("score", 6)

    output_shares = session.evaluate_public_polynomial(
        "score",
        (3, 2, 4),
        "poly_output",
    )

    assert reconstruct_secret(output_shares[:2]) == FieldElement(159, 251)
