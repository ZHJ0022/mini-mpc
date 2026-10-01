"""有限域归一化、算术与输入边界。 / Field normalization, arithmetic and input boundaries."""

import pytest

from dataclasses import FrozenInstanceError

from mini_mpc.math.field import FieldElement, is_prime


def test_normalizes_values_modulo_field_prime():
    assert FieldElement(8, 7).value == 1
    assert FieldElement(-1, 7).value == 6


def test_rejects_invalid_modulus():
    with pytest.raises(ValueError):
        FieldElement(1, 1)

    with pytest.raises(ValueError):
        FieldElement(1, 9)


def test_addition_and_subtraction():
    a = FieldElement(5, 7)
    b = FieldElement(6, 7)

    assert a + b == FieldElement(4, 7)
    assert a - b == FieldElement(6, 7)
    assert b - a == FieldElement(1, 7)


def test_multiplication_and_division():
    a = FieldElement(3, 7)
    b = FieldElement(5, 7)

    assert a * b == FieldElement(1, 7)
    assert b / a == FieldElement(4, 7)


def test_inverse():
    a = FieldElement(3, 7)

    assert a.inverse() == FieldElement(5, 7)
    assert a * a.inverse() == FieldElement(1, 7)


def test_zero_has_no_inverse():
    with pytest.raises(ZeroDivisionError):
        FieldElement(0, 7).inverse()


def test_power():
    assert FieldElement(3, 7) ** 3 == FieldElement(6, 7)


def test_operations_accept_public_integers():
    a = FieldElement(3, 7)

    assert a + 5 == FieldElement(1, 7)
    assert 5 + a == FieldElement(1, 7)
    assert a * 5 == FieldElement(1, 7)
    assert 5 * a == FieldElement(1, 7)
    assert 5 - a == FieldElement(2, 7)
    assert 6 / a == FieldElement(2, 7)


def test_rejects_elements_from_different_fields():
    a = FieldElement(3, 7)
    b = FieldElement(3, 11)

    with pytest.raises(ValueError):
        _ = a + b

    with pytest.raises(ValueError):
        _ = a * b


@pytest.mark.parametrize("value,modulus", [
    (True, 251), (False, 251), (1.5, 251), ("1", 251),
    (1, True), (1, 251.0), (1, "251"),
])
def test_field_rejects_non_integer_values_and_moduli(value, modulus):
    with pytest.raises(TypeError, match="integers"):
        FieldElement(value, modulus)


@pytest.mark.parametrize("value", [True, False, 2.5, "251"])
def test_prime_check_rejects_non_integers(value):
    with pytest.raises(TypeError, match="integer"):
        is_prime(value)


def test_field_values_cannot_mutate_stored_shares():
    from mini_mpc.runtime.party import MPCParty
    from mini_mpc.sharing.share import Share

    # store 保存对象引用；不可变域元素阻止绕过冲突写入检查。
    # The store retains references; immutable field values protect conflict checks.
    share = Share(1, FieldElement(3, 7), 2, 3)
    party = MPCParty(1)
    party.receive_share("immutable-field", "input", share)
    with pytest.raises(FrozenInstanceError):
        share.value.value = 4
    assert party.get_share("immutable-field", "input").value == FieldElement(3, 7)


@pytest.mark.parametrize("exponent", [True, 1.5])
def test_field_power_rejects_non_integer_exponents(exponent):
    with pytest.raises(TypeError, match="integer"):
        FieldElement(3, 7) ** exponent
