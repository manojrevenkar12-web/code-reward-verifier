"""The codec for values that cross the trust boundary.

A completion runs in a sandbox; the tests run outside it. What comes back from the sandbox is data, never
objects: only exact built-in types are encoded (a subclass of ``int`` with an ``__eq__`` that always returns
True is refused, not passed along). The decoder treats every frame as hostile: it checks the schema, the
size, the depth and the number of nodes before building anything.

Wire format: JSON arrays tagged by type, e.g. ``["tuple", [["int", "0x1"], ["str", "a"]]]``. Integers travel
as hex strings (no digit limit, no float rounding), floats as ``float.hex`` (exact, keeps -0.0, inf, nan).
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import types
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any


class EncodeError(Exception):
    """The value cannot cross the boundary (wrong type, too large, too deep)."""


class DecodeError(Exception):
    """The frame is not a well-formed encoding (a protocol violation by the sandboxed side)."""


@dataclass(frozen=True)
class Limits:
    max_nodes: int = 200_000
    max_bytes: int = 8 * 1024 * 1024
    max_depth: int = 64


DEFAULT = Limits()

_INT = re.compile(r"-?0x[0-9a-f]+")
_FLOAT = re.compile(r"-?(0x[0-9a-f](\.[0-9a-f]*)?p[+-][0-9]+|inf|nan)")
# Iterators from these C modules are materialised (map, filter, zip, generators, itertools.*); a
# user-defined iterator class is refused like any other object.
_ITERATOR_MODULES = frozenset({"builtins", "itertools"})
_GENERATOR = types.GeneratorType


class _Budget:
    def __init__(self, limits: Limits, error: type[Exception]) -> None:
        self.limits = limits
        self.nodes = 0
        self.bytes = 0
        self.error = error

    def spend(self, nodes: int = 1, size: int = 0) -> None:
        self.nodes += nodes
        self.bytes += size
        if self.nodes > self.limits.max_nodes or self.bytes > self.limits.max_bytes:
            lim = self.limits
            raise self.error(f"value too large (> {lim.max_nodes} nodes or {lim.max_bytes} bytes)")

    def depth(self, depth: int) -> None:
        if depth > self.limits.max_depth:
            raise self.error(f"value too deep (> {self.limits.max_depth} levels)")


def _hex_int(n: int) -> str:
    return ("-0x" + format(-n, "x")) if n < 0 else ("0x" + format(n, "x"))


def encode(value: object, limits: Limits = DEFAULT) -> str:
    """Encode plain data; raise EncodeError for anything else. Runs inside the sandbox."""
    budget = _Budget(limits, EncodeError)
    return json.dumps(_enc(value, budget, 0), separators=(",", ":"))


def _enc(v: Any, b: _Budget, depth: int) -> list[Any]:
    b.depth(depth)
    b.spend()
    t = type(v)
    if v is None:
        return ["none"]
    if t is bool:
        return ["bool", v]
    if t is int:
        b.spend(size=v.bit_length() // 4 + 1)
        return ["int", _hex_int(v)]
    if t is float:
        return ["float", v.hex()]
    if t is complex:
        return ["complex", [v.real.hex(), v.imag.hex()]]
    if t is str:
        b.spend(size=len(v))
        return ["str", v]
    if t is bytes:
        b.spend(size=len(v))
        return ["bytes", base64.b64encode(v).decode("ascii")]
    if t is range:
        return ["range", [_hex_int(v.start), _hex_int(v.stop), _hex_int(v.step)]]
    if t in (list, tuple, set, frozenset):
        name = t.__name__
        items = sorted(v, key=_order_key) if t in (set, frozenset) else v
        return [name, [_enc(x, b, depth + 1) for x in items]]
    if t is dict:
        return ["dict", [[_enc(k, b, depth + 1), _enc(x, b, depth + 1)] for k, x in v.items()]]
    if t is _GENERATOR or (isinstance(v, Iterator) and t.__module__ in _ITERATOR_MODULES):
        out: list[Any] = []
        for x in v:
            out.append(_enc(x, b, depth + 1))
        return ["iter", out]
    raise EncodeError(f"cannot return a value of type {t.__qualname__}: only plain built-in data crosses the sandbox")


def _order_key(x: object) -> tuple[str, str]:
    # Sets have no order; sort members so the same set always encodes to the same frame.
    return (type(x).__name__, repr(x))


def decode(frame: str, limits: Limits = DEFAULT) -> object:
    """Decode a frame produced by `encode`. Runs on the trusted side; every frame is hostile."""
    if len(frame) > limits.max_bytes:
        raise DecodeError("frame too large")
    try:
        tree = json.loads(frame)
    except (ValueError, RecursionError) as e:
        raise DecodeError(f"not a JSON frame: {e}") from None
    budget = _Budget(limits, DecodeError)
    return _dec(tree, budget, 0)


def _dec(node: object, b: _Budget, depth: int) -> object:
    b.depth(depth)
    b.spend()
    if not isinstance(node, list) or not node or not isinstance(node[0], str):
        raise DecodeError("node must be a [tag, payload] array")
    tag = node[0]
    if tag == "none" and len(node) == 1:
        return None
    if len(node) != 2:
        raise DecodeError(f"bad arity for {tag!r}")
    p = node[1]
    match tag:
        case "bool" if type(p) is bool:
            return p
        case "int" if isinstance(p, str):
            return _dec_int(p, b)
        case "float" if isinstance(p, str):
            return _dec_float(p)
        case "complex" if isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p):
            return complex(_dec_float(p[0]), _dec_float(p[1]))
        case "str" if isinstance(p, str):
            b.spend(size=len(p))
            return p
        case "bytes" if isinstance(p, str):
            b.spend(size=len(p))
            try:
                return base64.b64decode(p, validate=True)
            except (binascii.Error, ValueError):
                raise DecodeError("bad base64") from None
        case "range" if isinstance(p, list) and len(p) == 3 and all(isinstance(x, str) for x in p):
            start, stop, step = (_dec_int(x, b) for x in p)
            if step == 0:
                raise DecodeError("range step 0")
            return range(start, stop, step)
        case "list" | "tuple" | "set" | "frozenset" | "iter" if isinstance(p, list):
            items = [_dec(x, b, depth + 1) for x in p]
            try:
                if tag == "list":
                    return items
                if tag == "tuple":
                    return tuple(items)
                if tag == "set":
                    return set(items)
                if tag == "frozenset":
                    return frozenset(items)
            except TypeError:
                raise DecodeError(f"unhashable member in {tag}") from None
            return iter(items)
        case "dict" if isinstance(p, list):
            out: dict[object, object] = {}
            for pair in p:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise DecodeError("dict entries must be [key, value]")
                key = _dec(pair[0], b, depth + 1)
                try:
                    out[key] = _dec(pair[1], b, depth + 1)
                except TypeError:
                    raise DecodeError("unhashable dict key") from None
            return out
    raise DecodeError(f"unknown or malformed node {tag!r}")


def _dec_int(s: str, b: _Budget) -> int:
    if not _INT.fullmatch(s):
        raise DecodeError("bad int")
    b.spend(size=len(s))
    return int(s, 16)


def _dec_float(s: str) -> float:
    if not _FLOAT.fullmatch(s):
        raise DecodeError("bad float")
    if s in ("inf", "-inf", "nan"):
        return float(s)
    try:
        return float.fromhex(s)
    except (ValueError, OverflowError):
        raise DecodeError("bad float") from None
