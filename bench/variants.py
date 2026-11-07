"""Completions to verify: honest ones (the reference solution and simple mutants of it) and attacks.

Mutants are textual and deterministic so anyone can regenerate them: the first applicable of a few classic
mutation operators, plus "return None". Some mutants are equivalent to the original and still pass; that
is fine, because both scorers see the same text and only their agreement is measured."""

from __future__ import annotations

from dataclasses import dataclass

from crv.datasets import Example

_OPERATORS = [(" < ", " <= "), (" > ", " >= "), (" + ", " - "), (" == ", " != "), (" - ", " + "), (" * ", " + ")]


@dataclass(frozen=True)
class Variant:
    example: Example
    kind: str  # "reference" | "mutant" | attack name
    completion: str


def honest(example: Example) -> list[Variant]:
    out = [Variant(example, "reference", example.reference)]
    ref = example.reference
    for old, new in _OPERATORS:
        if old in ref:
            out.append(Variant(example, "mutant", ref.replace(old, new, 1)))
            break
    if "return " in ref:
        out.append(Variant(example, "mutant", ref.replace("return ", "return None  # ", 1)))
    return out
