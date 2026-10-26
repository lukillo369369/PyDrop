"""Typed runtime wrappers embedded in exports that use executable libraries."""
import math as _library_math


class LibraryNodeError(ValueError):
    """An executable node violated its declared port interface."""


def _library_checked(value, port, label):
    kind = port["type"]
    if kind == "Any":
        return value
    valid = ((kind == "Number" and type(value) in (int, float)) or
             (kind == "Text" and isinstance(value, str)) or
             (kind == "Boolean" and type(value) is bool))
    if kind == "Quantity":
        quantity_type = globals().get("_Quantity")
        if quantity_type is not None and isinstance(value, quantity_type):
            try:
                return value.to(port["unit"])
            except ValueError as exc:
                raise LibraryNodeError(f"{label}: {exc}") from None
    if not valid:
        raise LibraryNodeError(f"{label} expects {kind}; received {type(value).__name__}. Use a compatible value or an explicit conversion.")
    if kind == "Number" and (abs(value) > 1e100 or not _library_math.isfinite(value)):
        raise LibraryNodeError(f"{label} requires a finite Number of magnitude at most 1e100.")
    return value


def _library_call(implementation, inputs, outputs, label, arguments):
    checked = [_library_checked(value, port, label + "." + port["name"])
               for port, value in zip(inputs, arguments)]
    result = implementation(*checked)
    expected = {port["name"] for port in outputs}
    if not isinstance(result, dict) or set(result) != expected:
        raise LibraryNodeError(f"{label} must return a dict with exactly these outputs: {', '.join(port['name'] for port in outputs)}.")
    return {port["name"]: _library_checked(result[port["name"]], port, label + "." + port["name"])
            for port in outputs}
