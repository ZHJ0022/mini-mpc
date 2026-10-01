"""多项式求值、次数与插值约束。 / Polynomial evaluation, degree and interpolation."""

import pytest

from mini_mpc.math.field import FieldElement
from mini_mpc.math.polynomial import Polynomial, interpolate_coefficients


def test_polynomial_evaluation_with_integer_x():
    polynomial = Polynomial(
        [
            FieldElement(2, 7),
            FieldElement(3, 7),
            FieldElement(4, 7),
        ]
    )

    assert polynomial.evaluate(2) == FieldElement(3, 7)


def test_polynomial_evaluation_with_field_element_x():
    polynomial = Polynomial(
        [
            FieldElement(1, 11),
            FieldElement(2, 11),
            FieldElement(3, 11),
        ]
    )

    assert polynomial.evaluate(FieldElement(4, 11)) == FieldElement(2, 11)


def test_degree_ignores_trailing_zero_coefficients():
    polynomial = Polynomial(
        [
            FieldElement(5, 7),
            FieldElement(0, 7),
            FieldElement(0, 7),
        ]
    )

    assert polynomial.degree() == 0
    assert polynomial.evaluate(3) == FieldElement(5, 7)


def test_rejects_empty_coefficients():
    with pytest.raises(ValueError):
        Polynomial([])


def test_rejects_non_field_coefficients():
    with pytest.raises(TypeError):
        Polynomial([1, 2, 3])


def test_rejects_coefficients_from_different_fields():
    with pytest.raises(ValueError):
        Polynomial([FieldElement(1, 7), FieldElement(2, 11)])


def test_rejects_evaluation_point_from_different_field():
    polynomial = Polynomial([FieldElement(1, 7), FieldElement(2, 7)])

    with pytest.raises(ValueError):
        polynomial.evaluate(FieldElement(3, 11))


def test_interpolate_coefficients_rebuilds_polynomial():
    coefficients = interpolate_coefficients(
        [(0, 3), (1, 0), (2, 6)],
        modulus=11,
    )
    polynomial = Polynomial(coefficients)

    assert polynomial.evaluate(0) == FieldElement(3, 11)
    assert polynomial.evaluate(1) == FieldElement(0, 11)
    assert polynomial.evaluate(2) == FieldElement(6, 11)


def test_interpolate_coefficients_rejects_duplicate_x_values():
    with pytest.raises(ValueError):
        interpolate_coefficients([(1, 3), (1, 4)], modulus=11)
