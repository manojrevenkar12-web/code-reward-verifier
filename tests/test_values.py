"""The value codec is the trust boundary: results cross it from the sandbox to the trusted side. Only plain
data of exact built-in types crosses; anything that could carry its own behaviour (an __eq__ that always says
yes) is refused, and the decoder treats every frame as hostile."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from crv.domain.values import DecodeError, EncodeError, Limits, decode, encode

plain = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False)
    | st.text(max_size=20)
    | st.binary(max_size=20)
    | st.complex_numbers(allow_nan=False),
    lambda inner: st.lists(inner, max_size=5)
    | st.lists(inner, max_size=5).map(tuple)
    | st.frozensets(st.integers() | st.text(max_size=5), max_size=5)
    | st.sets(st.integers() | st.text(max_size=5), max_size=5)
    | st.dictionaries(st.integers() | st.text(max_size=5), inner, max_size=5),
    max_leaves=30,
)


def same(a: object, b: object) -> bool:
    """Equality that also tells types apart (1 vs True vs 1.0) and -0.0 from 0.0."""
    if type(a) is not type(b):
        return False
    if isinstance(a, float):
        return a.hex() == b.hex()  # type: ignore[union-attr]
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))  # type: ignore[arg-type]
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)  # type: ignore[attr-defined,index]
    return a == b


@given(plain)
def test_plain_data_round_trips_exactly(value: object) -> None:
    assert same(decode(encode(value)), value)


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), -0.0, 5e-324, 2**200, -(2**200)])
def test_edge_numbers_round_trip(value: object) -> None:
    assert same(decode(encode(value)), value)


def test_nan_round_trips_as_nan() -> None:
    assert math.isnan(decode(encode(float("nan"))))  # type: ignore[arg-type]


def test_custom_eq_object_is_refused() -> None:
    class AlwaysEqual:
        def __eq__(self, other: object) -> bool:
            return True

    with pytest.raises(EncodeError, match="AlwaysEqual"):
        encode(AlwaysEqual())


@pytest.mark.parametrize("base", [int, str, list, tuple, dict, float])
def test_subclasses_of_builtins_are_refused(base: type) -> None:
    sneaky = type("Sneaky", (base,), {"__eq__": lambda self, other: True, "__hash__": lambda self: 0})
    with pytest.raises(EncodeError, match="Sneaky"):
        encode(sneaky())


def test_refusal_reaches_nested_values() -> None:
    class Bad:
        pass

    with pytest.raises(EncodeError):
        encode([1, (2, {"k": Bad()})])


def test_iterators_are_materialised_and_stay_one_shot_iterators() -> None:
    out = decode(encode(map(abs, [-1, -2])))
    assert out != [1, 2]  # an iterator is not equal to a list, as in plain Python
    assert list(out) == [1, 2]  # type: ignore[call-overload]
    assert list(out) == []  # type: ignore[call-overload]


def test_range_round_trips() -> None:
    assert decode(encode(range(2, 10, 3))) == range(2, 10, 3)


def test_size_budget_is_enforced_while_encoding() -> None:
    with pytest.raises(EncodeError, match="too large"):
        encode(list(range(1000)), Limits(max_nodes=100))
    with pytest.raises(EncodeError, match="too large"):
        encode("x" * 5000, Limits(max_bytes=1000))


def test_cycles_are_refused_not_followed() -> None:
    loop: list[object] = []
    loop.append(loop)
    with pytest.raises(EncodeError, match="too deep"):
        encode(loop)


def test_infinite_iterator_hits_the_budget() -> None:
    import itertools

    with pytest.raises(EncodeError, match="too large"):
        encode(itertools.count(), Limits(max_nodes=500))


@pytest.mark.parametrize(
    "frame",
    [
        "not json",
        '["int", 5]',  # ints travel as strings
        '["int", "12x"]',
        '["float", "1.5"]',  # floats travel as hex
        '["list", "abc"]',
        '["dict", [[1]]]',
        '["set", [["list", []]]]',  # unhashable member
        '["exec", "import os"]',
        '["str"]',
        "[]",
        '{"t": "int"}',
        '["bytes", "@@notbase64@@"]',
        '["bool", 1]',
    ],
)
def test_decoder_rejects_malformed_frames(frame: str) -> None:
    with pytest.raises(DecodeError):
        decode(frame)


def test_decoder_enforces_its_own_budget_on_hostile_frames() -> None:
    deep = '["list", [' * 500 + '["none"]' + "]]" * 500
    with pytest.raises(DecodeError):
        decode(deep)
    big = '["list", [' + ",".join(['["none"]'] * 5000) + "]]"
    with pytest.raises(DecodeError):
        decode(big, Limits(max_nodes=1000))
