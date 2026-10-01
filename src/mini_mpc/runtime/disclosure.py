from collections.abc import Sequence
from dataclasses import dataclass

from mini_mpc.math.field import FieldElement
from mini_mpc.sharing.shamir import reconstruct_secret
from mini_mpc.sharing.share import Share


@dataclass(frozen=True)
class OutputDisclosurePolicy:
    """Policy for selective output reconstruction.

    输出披露原则是：只有指定接收者可以获得足够数量的 output shares 并重构结果。
    The output-disclosure principle is that only the designated receiver may
    obtain enough output shares to reconstruct the result.

    当前策略对象只记录接收者身份和重构所需的最少 shares 数量。
    The current policy object records only the receiver identity and the minimum
    number of shares required for reconstruction.
    """

    receiver_id: str
    min_shares: int

    def __post_init__(self) -> None:
        """Require a named receiver and a positive integer share count.

        接收者不能为空，最少份额数必须是正整数。
        """

        if not self.receiver_id:
            raise ValueError("receiver_id must not be empty")
        if type(self.min_shares) is not int:
            raise TypeError("min_shares must be an integer")
        if self.min_shares < 1:
            raise ValueError("min_shares must be at least 1")


def reveal_to_receiver(
    output_shares: Sequence[Share],
    policy: OutputDisclosurePolicy,
    receiver_id: str,
) -> FieldElement:
    """Check the receiver policy and reconstruct an already delivered output.

    校验接收者与份额数量，再重构已交付的输出。
    此函数用于接收端；网络交付权限由服务端 mTLS 与任务授权检查。
    This receiver-side helper assumes delivery; the service authorizes delivery
    through mTLS and task checks.
    """

    if receiver_id != policy.receiver_id:
        raise PermissionError("receiver is not authorized to reconstruct output")
    if len(output_shares) < policy.min_shares:
        raise ValueError("not enough output shares for this disclosure policy")

    return reconstruct_secret(list(output_shares[: policy.min_shares]))
