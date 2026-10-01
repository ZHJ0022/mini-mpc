"""Role-level rules for requests handled by an MPC party service.

MPC party 服务的角色级请求规则；会话与步骤由任务登记和执行层校验。
Role checks live here so HTTP handlers do not duplicate access decisions.
"""

from __future__ import annotations

from typing import Final


# 特征归属由当前业务模型确定，提交方不能在请求中自行声明所有者。
# The business model fixes feature ownership; callers cannot declare it themselves.
FEATURE_OWNERS: Final = {
    "telco_absence_hours": "telco",
    "telco_max_absence_streak": "telco",
    "clinical_need_score": "hospital",
}

DATA_ROLES: Final = frozenset({"insurer", "telco", "hospital"})
INSURER_OPERATIONS: Final = frozenset({
    "register_task",
    "compute",
    "submit_public_constant",
    "read_result",
})


def party_id_from_identity(identity: str) -> int | None:
    """Parse an authenticated party identity, or return None.

    从已认证的身份解析 party 编号；普通机构身份返回 None。
    Parse a party id from an authenticated identity, or return None for a data holder.
    """

    if not isinstance(identity, str) or not identity.startswith("party-"):
        return None
    suffix = identity.removeprefix("party-")
    if not suffix.isascii() or not suffix.isdecimal() or suffix.startswith("0"):
        return None
    party_id = int(suffix)
    return party_id if party_id > 0 else None


def authorize_operation(
    identity: str,
    operation: str,
    *,
    secret_id: str | None = None,
    source_party_id: int | None = None,
) -> None:
    """Reject operations outside the authenticated node's role.

    按可信节点身份执行默认拒绝的角色检查；不从请求体获取角色。
    Check the trusted node identity and deny unknown operations by default.

    这里只检查角色；任务登记与执行层检查会话状态和精确协议步骤。
    This gate checks roles; task registration and execution check state and steps.
    """

    party_id = party_id_from_identity(identity)
    if identity not in DATA_ROLES and party_id is None:
        raise PermissionError("unknown node identity")

    if operation == "health":
        return
    if operation == "submit_feature":
        if secret_id is not None and FEATURE_OWNERS.get(secret_id) == identity:
            return
    elif operation == "submit_reshare":
        if party_id is not None and source_party_id == party_id:
            return
    elif operation in INSURER_OPERATIONS:
        if identity == "insurer":
            return

    raise PermissionError("operation is not permitted for node identity")
