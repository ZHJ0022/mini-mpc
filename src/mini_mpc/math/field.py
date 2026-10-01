from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, repr=False)
class FieldElement:
    """Immutable element of the prime field F_p.

    素数域 F_p 的不可变元素；避免已存储份额通过外部引用被修改。
    """

    value: int
    modulus: int

    def __post_init__(self) -> None:
        # bool 是 int 的子类；域元素和模数只接受真正的整数。
        # bool subclasses int; field values and moduli require exact integers.
        if type(self.value) is not int or type(self.modulus) is not int:
            raise TypeError("value and modulus must be integers")
        if self.modulus <= 1:
            raise ValueError("modulus must be greater than 1")
        if not is_prime(self.modulus):
            raise ValueError("modulus must be prime")
        # 仅构造时归一化；负整数按模数编码，此后不修改对象。
        # Normalize during construction; negative integers are encoded modulo p.
        object.__setattr__(self, "value", self.value % self.modulus)

    def __repr__(self) -> str:
        return f"FieldElement({self.value}, modulus={self.modulus})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FieldElement):
            return False
        return self.value == other.value and self.modulus == other.modulus

    def __add__(self, other: int | FieldElement) -> FieldElement:
        other = self._as_same_field(other)
        return FieldElement(self.value + other.value, self.modulus)

    def __radd__(self, other: int | FieldElement) -> FieldElement:
        return self + other

    def __sub__(self, other: int | FieldElement) -> FieldElement:
        other = self._as_same_field(other)
        return FieldElement(self.value - other.value, self.modulus)

    def __rsub__(self, other: int | FieldElement) -> FieldElement:
        return FieldElement(other, self.modulus) - self

    def __neg__(self) -> FieldElement:
        return FieldElement(-self.value, self.modulus)

    def __mul__(self, other: int | FieldElement) -> FieldElement:
        other = self._as_same_field(other)
        return FieldElement(self.value * other.value, self.modulus)

    def __rmul__(self, other: int | FieldElement) -> FieldElement:
        return self * other

    def __truediv__(self, other: int | FieldElement) -> FieldElement:
        other = self._as_same_field(other)
        return self * other.inverse()

    def __rtruediv__(self, other: int | FieldElement) -> FieldElement:
        return FieldElement(other, self.modulus) / self

    def __pow__(self, exponent: int) -> FieldElement:
        if type(exponent) is not int:
            raise TypeError("exponent must be an integer")
        return FieldElement(pow(self.value, exponent, self.modulus), self.modulus)

    def inverse(self) -> FieldElement:
        """Return the multiplicative inverse of a nonzero element.

        返回非零元素的乘法逆元。
        """
        if self.value == 0:
            raise ZeroDivisionError("zero has no inverse")
        return FieldElement(pow(self.value, -1, self.modulus), self.modulus)

    def _as_same_field(self, other: int | FieldElement) -> FieldElement:
        if isinstance(other, int):
            return FieldElement(other, self.modulus)
        if not isinstance(other, FieldElement):
            raise TypeError("operation requires an int or FieldElement")
        if self.modulus != other.modulus:
            raise ValueError("field elements must have the same modulus")
        return other


def is_prime(value: int) -> bool:
    """Check the prime modulus required by field division and interpolation.

    校验域除法和插值所需的素数模数。
    """
    if type(value) is not int:
        raise TypeError("value must be an integer")
    if value <= 1:
        return False
    if value <= 3:
        return True
    if value % 2 == 0:
        return False
    divisor = 3
    while divisor * divisor <= value:
        if value % divisor == 0:
            return False
        divisor += 2
    return True
