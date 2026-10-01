from collections.abc import Sequence

from mini_mpc.math.field import FieldElement


class Polynomial:
    """Polynomial over one finite field.

    系数按次数从低到高保存；求值使用 Horner 法。
    Coefficients are stored from low degree to high degree:
    coefficients[0] + coefficients[1] * x + ...
    """

    def __init__(self, coefficients: Sequence[FieldElement]) -> None:
        if not coefficients:
            raise ValueError("polynomial needs at least one coefficient")

        self.coefficients = list(coefficients)

        for coefficient in self.coefficients:
            if not isinstance(coefficient, FieldElement):
                raise TypeError("coefficients must be FieldElement instances")

        self.modulus = self.coefficients[0].modulus

        for coefficient in self.coefficients:
            if coefficient.modulus != self.modulus:
                raise ValueError("all coefficients must use the same modulus")

        self._remove_trailing_zeroes()

    def degree(self) -> int:
        return len(self.coefficients) - 1

    def evaluate(self, x: int | FieldElement) -> FieldElement:
        if isinstance(x, int):
            x = FieldElement(x, self.modulus)
        if not isinstance(x, FieldElement):
            raise TypeError("x must be an int or FieldElement")
        if x.modulus != self.modulus:
            raise ValueError("x must use the same modulus as the polynomial")

        result = FieldElement(0, self.modulus)
        for coefficient in reversed(self.coefficients):
            result = result * x + coefficient
        return result

    def _remove_trailing_zeroes(self) -> None:
        while len(self.coefficients) > 1 and self.coefficients[-1].value == 0:
            self.coefficients.pop()


def interpolate_coefficients(
    points: Sequence[tuple[int | FieldElement, int | FieldElement]],
    modulus: int,
) -> list[FieldElement]:
    """Interpolate polynomial coefficients from field points.

    从有限域点插值得到多项式系数。
    Interpolate polynomial coefficients from points over one finite field.
    """

    if not points:
        raise ValueError("points must not be empty")

    x_values = [_to_field_element(x_value, modulus) for x_value, _ in points]
    y_values = [_to_field_element(y_value, modulus) for _, y_value in points]
    if len({x_value.value for x_value in x_values}) != len(x_values):
        raise ValueError("x values must be distinct")

    size = len(points)
    matrix = []
    for x_value, y_value in zip(x_values, y_values, strict=True):
        row = []
        power = FieldElement(1, modulus)
        for _ in range(size):
            row.append(power)
            power = power * x_value
        row.append(y_value)
        matrix.append(row)

    _solve_field_linear_system(matrix)
    return [matrix[row_index][-1] for row_index in range(size)]


def _to_field_element(value: int | FieldElement, modulus: int) -> FieldElement:
    """Convert one interpolation value to the target field.

    将插值输入转换到目标有限域。
    Convert an interpolation input to the target field.
    """

    if isinstance(value, int):
        return FieldElement(value, modulus)
    if not isinstance(value, FieldElement):
        raise TypeError("points must contain int or FieldElement values")
    if value.modulus != modulus:
        raise ValueError("points must use the interpolation modulus")
    return value


def _solve_field_linear_system(
    matrix: list[list[FieldElement]],
) -> None:
    """Solve an augmented field matrix in place.

    原地求解有限域增广矩阵。
    Solve an augmented matrix over the field in place.
    """

    size = len(matrix)
    for column in range(size):
        pivot_row = None
        for row in range(column, size):
            if matrix[row][column].value != 0:
                pivot_row = row
                break
        if pivot_row is None:
            raise ValueError("points do not define a unique polynomial")

        if pivot_row != column:
            matrix[column], matrix[pivot_row] = matrix[pivot_row], matrix[column]

        pivot_inverse = matrix[column][column].inverse()
        for item_index in range(column, size + 1):
            matrix[column][item_index] = matrix[column][item_index] * pivot_inverse

        for row in range(size):
            if row == column:
                continue
            factor = matrix[row][column]
            if factor.value == 0:
                continue
            for item_index in range(column, size + 1):
                matrix[row][item_index] = (
                    matrix[row][item_index]
                    - factor * matrix[column][item_index]
                )
