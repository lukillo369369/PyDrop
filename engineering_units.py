"""Small SI quantity runtime, embedded verbatim in standalone exports.

Dimensions are (length, mass, time, current). Only multiplicative units are
supported: no temperature offsets, angle semantics, or automatic unit guessing.
"""
import ast as _unit_ast
import math as _unit_math
import re as _unit_re


class UnitError(ValueError):
    """A dimensional mismatch suitable for display in the editor."""


_UNITLESS = (0, 0, 0, 0)
_LENGTH = (1, 0, 0, 0)
_MASS = (0, 1, 0, 0)
_TIME = (0, 0, 1, 0)
_CURRENT = (0, 0, 0, 1)
_FORCE = (1, 1, -2, 0)
_VOLTAGE = (2, 1, -3, -1)
_RESISTANCE = (2, 1, -3, -2)
_POWER = (2, 1, -3, 0)
_TORQUE = (2, 1, -2, 0)
_UNITS = {
    "1": (1, _UNITLESS),
    "m": (1, _LENGTH), "mm": (.001, _LENGTH), "cm": (.01, _LENGTH),
    "km": (1000, _LENGTH), "in": (.0254, _LENGTH), "ft": (.3048, _LENGTH),
    "kg": (1, _MASS), "g": (.001, _MASS), "lb": (.45359237, _MASS),
    "s": (1, _TIME), "ms": (.001, _TIME), "min": (60, _TIME), "h": (3600, _TIME),
    "A": (1, _CURRENT), "mA": (.001, _CURRENT),
    "N": (1, _FORCE), "kN": (1000, _FORCE), "lbf": (4.4482216152605, _FORCE),
    "V": (1, _VOLTAGE), "mV": (.001, _VOLTAGE), "kV": (1000, _VOLTAGE),
    "ohm": (1, _RESISTANCE), "kohm": (1000, _RESISTANCE), "Mohm": (1e6, _RESISTANCE),
    "W": (1, _POWER), "mW": (.001, _POWER), "kW": (1000, _POWER),
    "m/s": (1, (1, 0, -1, 0)), "m/s^2": (1, (1, 0, -2, 0)),
    "N*m": (1, _TORQUE), "N*mm": (.001, _TORQUE),
    "lbf*ft": (4.4482216152605 * .3048, _TORQUE),
}
UNIT_NAMES = tuple(_UNITS)
ENGINEERING = {
    "Force": ({"mass": "kg", "acceleration": "m/s^2"}, "N", "*"),
    "Torque": ({"force": "N", "distance": "m"}, "N*m", "*"),
    "Ohm's Law": ({"current": "A", "resistance": "ohm"}, "V", "*"),
    "Electrical Power": ({"voltage": "V", "current": "A"}, "W", "*"),
}


def _dim_combine(a, b, sign=1):
    return tuple(round(x + sign*y, 12) for x, y in zip(a, b))


def _dim_power(a, exponent):
    return tuple(round(x * exponent, 12) for x in a)


def dimension_name(dim):
    names = {_UNITLESS: "unitless", _LENGTH: "length", _MASS: "mass",
             _TIME: "time", _CURRENT: "current", _FORCE: "force",
             _VOLTAGE: "voltage", _RESISTANCE: "resistance", _POWER: "power",
             _TORQUE: "torque", (1, 0, -2, 0): "acceleration"}
    return names.get(dim, " * ".join(f"{name}^{power:g}" for name, power in
                     zip(("m", "kg", "s", "A"), dim) if power) or "unitless")


def unit_spec(text):
    """Parse a bounded product/quotient of known units and integer powers."""
    if not isinstance(text, str) or not text or len(text) > 80:
        raise UnitError("Choose a unit such as m, kg, s, N, V, A, ohm, or 1 (unitless).")
    if text in _UNITS:
        return _UNITS[text]
    try:
        # "in" is a Python keyword, but remains a valid inch symbol in products.
        source = _unit_re.sub(r"\bin\b", "_inch", text.replace("^", "**"))
        tree = _unit_ast.parse(source, mode="eval")
        if len(list(_unit_ast.walk(tree))) > 40:
            raise ValueError()

        def parse(node):
            if isinstance(node, _unit_ast.Name) and node.id == "_inch":
                return _UNITS["in"]
            if isinstance(node, _unit_ast.Name) and node.id in _UNITS:
                return _UNITS[node.id]
            if isinstance(node, _unit_ast.Constant) and type(node.value) is int and node.value == 1:
                return 1, _UNITLESS
            if isinstance(node, _unit_ast.BinOp):
                scale, dim = parse(node.left)
                if isinstance(node.op, _unit_ast.Pow):
                    exponent = _unit_ast.literal_eval(node.right)
                    if type(exponent) is not int or abs(exponent) > 8:
                        raise ValueError()
                    return scale**exponent, _dim_power(dim, exponent)
                other, other_dim = parse(node.right)
                if isinstance(node.op, _unit_ast.Mult):
                    return scale*other, _dim_combine(dim, other_dim)
                if isinstance(node.op, _unit_ast.Div):
                    return scale/other, _dim_combine(dim, other_dim, -1)
            raise ValueError()

        scale, dim = parse(tree.body)
        if not _unit_math.isfinite(scale) or scale <= 0:
            raise ValueError()
        return scale, dim
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError, RecursionError):
        raise UnitError(f"Unknown or unsupported unit {text!r}. Use known symbols with *, /, and integer powers, e.g. kg*m/s^2.") from None


def _unit_finite(value):
    if type(value) not in (int, float):
        raise UnitError("Quantity magnitudes must be numbers; use Convert to Number for text.")
    value = float(value)
    if not _unit_math.isfinite(value) or abs(value) > 1e100:
        raise UnitError("Quantity result is non-finite or exceeds 1e100.")
    return value


class _Quantity:
    """Store SI magnitude; display units never affect arithmetic dimensions."""

    def __init__(self, value, unit="1"):
        scale, self.dim = unit_spec(unit)
        self.si = _unit_finite(_unit_finite(value) * scale)
        self.unit = unit

    @classmethod
    def _from_si(cls, value, dim, unit=None):
        result = cls.__new__(cls)
        result.si, result.dim = _unit_finite(value), dim
        result.unit = unit or next((name for name, (scale, d) in _UNITS.items()
                                   if scale == 1 and d == dim), None)
        return result

    @staticmethod
    def _coerce(value):
        return value if isinstance(value, _Quantity) else _Quantity(value)

    def _compatible(self, other, operation):
        other = self._coerce(other)
        if self.dim != other.dim:
            raise UnitError(f"Dimension mismatch in {operation}: {dimension_name(self.dim)} versus {dimension_name(other.dim)}. Use compatible quantities; a unit conversion cannot change dimensions.")
        return other

    def to(self, unit):
        _, dim = unit_spec(unit)
        if self.dim != dim:
            raise UnitError(f"Cannot convert {dimension_name(self.dim)} to {unit} ({dimension_name(dim)}). Choose a compatible unit.")
        return self._from_si(self.si, self.dim, unit)

    def __add__(self, other):
        return self._from_si(self.si + self._compatible(other, "addition").si, self.dim, self.unit)

    __radd__ = __add__

    def __sub__(self, other):
        return self._from_si(self.si - self._compatible(other, "subtraction").si, self.dim, self.unit)

    def __rsub__(self, other):
        return self._coerce(other).__sub__(self)

    def __mul__(self, other):
        other = self._coerce(other)
        return self._from_si(self.si * other.si, _dim_combine(self.dim, other.dim))

    __rmul__ = __mul__

    def __truediv__(self, other):
        other = self._coerce(other)
        return self._from_si(self.si / other.si, _dim_combine(self.dim, other.dim, -1))

    def __rtruediv__(self, other):
        return self._coerce(other).__truediv__(self)

    def __floordiv__(self, other):
        other = self._compatible(other, "floor division")
        return self._from_si(self.si // other.si, _UNITLESS)

    def __rfloordiv__(self, other):
        return self._coerce(other).__floordiv__(self)

    def __mod__(self, other):
        return self._from_si(self.si % self._compatible(other, "remainder").si, self.dim, self.unit)

    def __rmod__(self, other):
        return self._coerce(other).__mod__(self)

    def __pow__(self, other):
        other = self._coerce(other)
        if other.dim != _UNITLESS:
            raise UnitError("Powers require a unitless exponent.")
        if self.dim != _UNITLESS and abs(other.si) > 16:
            raise UnitError("Quantity exponents must be between -16 and 16.")
        return self._from_si(self.si ** other.si, _dim_power(self.dim, other.si))

    def __rpow__(self, other):
        return self._coerce(other).__pow__(self)

    def __neg__(self):
        return self._from_si(-self.si, self.dim, self.unit)

    def __pos__(self):
        return self

    def __abs__(self):
        return self._from_si(abs(self.si), self.dim, self.unit)

    def __round__(self, digits=0):
        scale = unit_spec(self.unit)[0] if self.unit else 1
        return self._from_si(round(self.si / scale, digits) * scale, self.dim, self.unit)

    def __float__(self):
        if self.dim != _UNITLESS:
            raise UnitError(f"Cannot discard {dimension_name(self.dim)} units. Use Unit Convert to change display units.")
        return self.si

    def __bool__(self):
        if self.dim != _UNITLESS:
            raise UnitError("Compare a quantity with a compatible quantity before using it as a condition.")
        return bool(self.si)

    def __eq__(self, other):
        return self.si == self._compatible(other, "comparison").si

    def __ne__(self, other):
        return not self == other

    def __lt__(self, other):
        return self.si < self._compatible(other, "comparison").si

    def __le__(self, other):
        return self.si <= self._compatible(other, "comparison").si

    def __gt__(self, other):
        return self.si > self._compatible(other, "comparison").si

    def __ge__(self, other):
        return self.si >= self._compatible(other, "comparison").si

    def __str__(self):
        if self.unit:
            scale, _ = unit_spec(self.unit)
            suffix = "" if self.unit == "1" else " " + self.unit
            return f"{self.si / scale:.12g}{suffix}"
        unit = "*".join(name if power == 1 else f"{name}^{power:g}"
                        for name, power in zip(("m", "kg", "s", "A"), self.dim) if power)
        return f"{self.si:.12g} {unit}"

    __repr__ = __str__


def _make_quantity(value, unit):
    if isinstance(value, _Quantity):
        value = float(value)  # A dimensional quantity cannot become a magnitude.
    return _Quantity(value, unit)


def _convert_unit(value, unit):
    return _Quantity._coerce(value).to(unit)


def _engineering(kind, a, b):
    inputs, output, _ = ENGINEERING[kind]
    checked = []
    for (pin, unit), value in zip(inputs.items(), (a, b)):
        value = _Quantity._coerce(value)
        expected = unit_spec(unit)[1]
        if value.dim != expected:
            raise UnitError(f"{kind}.{pin} needs {dimension_name(expected)} ({unit}); received {dimension_name(value.dim)}. Connect a Quantity with compatible units.")
        checked.append(value)
    return (checked[0] * checked[1]).to(output)


def _unit_sqrt(value):
    return value ** .5 if isinstance(value, _Quantity) else _unit_math.sqrt(value)


def _unit_scalar(value, function):
    if isinstance(value, _Quantity):
        value = float(value)
    return function(value)


def _unit_sin(value):
    return _unit_scalar(value, _unit_math.sin)


def _unit_cos(value):
    return _unit_scalar(value, _unit_math.cos)


def _unit_tan(value):
    return _unit_scalar(value, _unit_math.tan)


def _unit_exp(value):
    return _unit_scalar(value, _unit_math.exp)


def _unit_log(value, base=None):
    result = _unit_scalar(value, _unit_math.log)
    return result if base is None else result / _unit_scalar(base, _unit_math.log)
