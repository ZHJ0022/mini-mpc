from collections.abc import Mapping

from mini_mpc.math.field import FieldElement
from mini_mpc.sharing.share import Share


def share_to_dict(share: Share) -> dict[str, int]:
    """Serialize a share for JSON transport.

    将 share 序列化为可通过 JSON 传输的普通字段。
    Serialize a share into plain fields that can be transported as JSON.
    """

    return {
        "party_id": share.party_id,
        "value": share.value.value,
        "modulus": share.value.modulus,
        "threshold": share.threshold,
        "num_parties": share.num_parties,
    }


def share_from_dict(payload: Mapping[str, object]) -> Share:
    """Deserialize a share from JSON transport fields.

    从 JSON 传输字段恢复 Share；重建时复用 Share/FieldElement 的校验。
    Deserialize a Share from JSON transport fields; reconstruction reuses
    Share/FieldElement validation.
    """

    party_id = _required_int(payload, "party_id")
    value = _required_int(payload, "value")
    modulus = _required_int(payload, "modulus")
    threshold = _required_int(payload, "threshold")
    num_parties = _required_int(payload, "num_parties")
    return Share(
        party_id=party_id,
        value=FieldElement(value, modulus),
        threshold=threshold,
        num_parties=num_parties,
    )


def _required_int(payload: Mapping[str, object], field_name: str) -> int:
    """Read a required integer transport field.

    读取必需的整数传输字段。
    Read one required integer transport field.
    """

    if field_name not in payload:
        raise KeyError(f"missing required field: {field_name}")
    value = payload[field_name]
    # JSON 布尔值不是整数参数，尽管 bool 在 Python 中继承 int。
    # JSON booleans are not integer parameters, although Python bool subclasses int.
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an integer")
    return value
