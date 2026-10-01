from dataclasses import dataclass

from mini_mpc.math.field import FieldElement


@dataclass(frozen=True)
class Share:
    """A Shamir share held by one MPC party.

    表示某一个 MPC 参与方持有的一份 Shamir 秘密共享份额。
    Represents one Shamir secret share held by one MPC party.

    它不只是 ``(party_id, value)``，还记录重构阈值和总参与方数量，
    这样后续做本地加法、公开标量乘法、重构时才能检查参数是否兼容。
    It stores not only ``(party_id, value)``, but also the reconstruction
    threshold and total party count so protocol operations can reject
    incompatible shares.

    Attributes:
        party_id:
            持有该 share 的参与方编号，必须从 1 开始且不超过 ``num_parties``。
            1-based party identifier. It must be positive and no
            larger than ``num_parties``.

        value:
            该参与方拿到的有限域元素，也就是多项式在 ``party_id`` 处的值。
            Field element assigned to this party, equal to the
            sharing polynomial evaluated at ``party_id``.

        threshold:
            重构秘密所需的最少 share 数量，即 Shamir 的 ``k``。
            Minimum number of shares required for reconstruction.
            若分享次数上界为 ``t``，则 ``threshold = t + 1``。
            This is Shamir's ``k``. For degree bound ``t``, ``threshold = t + 1``.

        num_parties:
            本次 sharing 中的总参与方数量。
            Total number of parties in this sharing instance.
    """

    party_id: int
    value: FieldElement
    threshold: int
    num_parties: int

    def __post_init__(self) -> None:
        """Validate share metadata as soon as the object is created.

        创建对象时立即校验元数据，避免非法 share 流入协议计算。
        Validate metadata immediately so malformed shares do not enter protocol
        computation.

        这里不验证 ``value.modulus`` 是否为素数；该假设由有限域层和调用方负责。
        This method does not prove that the modulus is prime; that assumption
        belongs to the field layer and caller.
        """

        if not isinstance(self.value, FieldElement):
            raise TypeError("value must be a FieldElement")
        for name in ("party_id", "threshold", "num_parties"):
            if type(getattr(self, name)) is not int:
                raise TypeError(f"{name} must be an integer")
        if self.party_id <= 0:
            raise ValueError("party_id must be positive")
        if self.threshold < 1:
            raise ValueError("threshold must be at least 1")
        if self.num_parties < self.threshold:
            raise ValueError("num_parties must be at least threshold")
        if self.party_id > self.num_parties:
            raise ValueError("party_id must be at most num_parties")

    def __add__(self, other: "Share") -> "Share":
        """Add two compatible shares held by the same party.

        对同一个 party 持有的两个兼容 shares 做本地加法，得到和的 share。
        Locally add two compatible shares held by the same party to obtain a
        share of the sum.
        """

        self._assert_compatible(other)
        return Share(
            party_id=self.party_id,
            value=self.value + other.value,
            threshold=self.threshold,
            num_parties=self.num_parties,
        )

    def __sub__(self, other: "Share") -> "Share":
        """Subtract two compatible shares held by the same party.

        对同一个 party 持有的两个兼容 shares 做本地减法，得到差的 share。
        Locally subtract two compatible shares held by the same party to obtain
        a share of the difference.
        """

        self._assert_compatible(other)
        return Share(
            party_id=self.party_id,
            value=self.value - other.value,
            threshold=self.threshold,
            num_parties=self.num_parties,
        )

    def __mul__(self, scalar: int) -> "Share":
        """Multiply this share by a public integer scalar.

        用公开整数标量乘以当前 share，得到被共享秘密乘以该标量后的 share。
        Multiply this share by a public integer scalar to obtain a share of the
        scaled secret.
        """

        return self.scale(scalar)

    def __rmul__(self, scalar: int) -> "Share":
        """Multiply this share by a public integer scalar from the left.

        支持 ``public_scalar * share`` 形式的公开标量乘法。
        Support public scalar multiplication in the ``public_scalar * share``
        form.
        """

        return self.scale(scalar)

    def scale(self, scalar: int) -> "Share":
        """Return a share scaled by a public integer.

        返回当前 share 的公开标量倍数；这里的 scalar 不是秘密输入。
        Return the public scalar multiple of this share; the scalar is not a
        secret input.
        """

        if type(scalar) is not int:
            raise TypeError("scalar must be an int")
        return Share(
            party_id=self.party_id,
            value=self.value * scalar,
            threshold=self.threshold,
            num_parties=self.num_parties,
        )

    def _assert_compatible(self, other: "Share") -> None:
        """Reject shares that cannot be combined by one local party.

        拒绝不能由同一个本地 party 合并的 shares。
        Reject shares that cannot be combined by the same local party.
        """

        if not isinstance(other, Share):
            raise TypeError("operation requires another Share")
        if self.party_id != other.party_id:
            raise ValueError("shares must have the same party_id")
        if self.value.modulus != other.value.modulus:
            raise ValueError("shares must use the same modulus")
        if self.threshold != other.threshold:
            raise ValueError("shares must use the same threshold")
        if self.num_parties != other.num_parties:
            raise ValueError("shares must use the same num_parties")
