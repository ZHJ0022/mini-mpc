"""mTLS 身份对应的操作权限。 / Operation permissions for authenticated mTLS identities."""

import pytest

from mini_mpc.network.authorization import authorize_operation, party_id_from_identity


@pytest.mark.parametrize(
    ("identity", "operation", "secret_id", "source_party_id"),
    [
        ("insurer", "register_task", None, None),
        ("insurer", "compute", None, None),
        ("insurer", "submit_public_constant", None, None),
        ("insurer", "read_result", None, None),
        ("telco", "submit_feature", "telco_absence_hours", None),
        ("telco", "submit_feature", "telco_max_absence_streak", None),
        ("hospital", "submit_feature", "clinical_need_score", None),
        ("party-2", "submit_reshare", None, 2),
        ("party-3", "health", None, None),
    ],
)
def test_authorization_allows_only_owned_operations(
    identity, operation, secret_id, source_party_id
):
    # 允许的行为由可信身份和固定特征归属决定。
    # Allowed behavior follows the trusted identity and fixed feature ownership.
    authorize_operation(
        identity, operation, secret_id=secret_id, source_party_id=source_party_id
    )


@pytest.mark.parametrize(
    ("identity", "operation", "secret_id", "source_party_id"),
    [
        ("anonymous", "health", None, None),
        ("party-0", "health", None, None),
        ("party-01", "health", None, None),
        ("telco", "register_task", None, None),
        ("hospital", "compute", None, None),
        ("hospital", "read_result", None, None),
        ("insurer", "submit_feature", "clinical_need_score", None),
        ("telco", "submit_feature", "clinical_need_score", None),
        ("hospital", "submit_feature", "telco_absence_hours", None),
        ("telco", "submit_feature", "unknown_feature", None),
        ("party-1", "submit_reshare", None, 2),
        ("party-1", "submit_feature", "telco_absence_hours", None),
        ("insurer", "get_share", None, None),
        ("party-1", "get_share", None, None),
    ],
)
def test_authorization_denies_unknown_or_cross_role_operations(
    identity, operation, secret_id, source_party_id
):
    # 任意 share 读取即使来自已认证节点也不能按角色放行。
    # Arbitrary share reads are denied even for authenticated identities.
    with pytest.raises(PermissionError):
        authorize_operation(
            identity, operation, secret_id=secret_id, source_party_id=source_party_id
        )


def test_party_identity_parser_rejects_noncanonical_values():
    assert party_id_from_identity("party-12") == 12
    assert party_id_from_identity("party-012") is None
    assert party_id_from_identity("party-1extra") is None
    assert party_id_from_identity("telco") is None
