"""FilterSet framework -- parameterized SQL predicates (no f-string injection)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Predicate:
    """A single WHERE clause with its bound parameters."""

    clause: str
    params: dict[str, Any]


class FilterSpec:
    """Describes how a single query parameter maps to a SQL predicate."""

    def __init__(
        self,
        column: str,
        param_name: str,
        param_type: str,
        op: str = "=",
    ) -> None:
        self.column = column
        self.param_name = param_name
        self.param_type = param_type
        self.op = op

    def to_predicate(self, value: Any) -> Predicate:
        return Predicate(
            clause=f"{self.column} {self.op} {{{self.param_name}:{self.param_type}}}",
            params={self.param_name: value},
        )


# -- Convenience constructors -------------------------------------------------


def EqFilter(
    column: str,
    param_name: str | None = None,
    param_type: str = "String",
) -> FilterSpec:
    return FilterSpec(column, param_name or column, param_type, "=")


def GteFilter(
    column: str,
    param_name: str | None = None,
    param_type: str = "Float64",
) -> FilterSpec:
    return FilterSpec(column, param_name or f"{column}_gte", param_type, ">=")


def LteFilter(
    column: str,
    param_name: str | None = None,
    param_type: str = "Float64",
) -> FilterSpec:
    return FilterSpec(column, param_name or f"{column}_lte", param_type, "<=")


# -- FilterSet ----------------------------------------------------------------


class FilterSet:
    """Build a WHERE clause from user-supplied query parameters.

    Only parameters whose value is not ``None`` produce predicates.
    """

    def __init__(
        self,
        params: dict[str, Any],
        allowed: dict[str, FilterSpec],
    ) -> None:
        self._predicates: list[Predicate] = []
        for key, spec in allowed.items():
            val = params.get(key)
            if val is not None:
                self._predicates.append(spec.to_predicate(val))

    def to_sql(self) -> tuple[str, dict[str, Any]]:
        """Return ``(where_clause, params)`` ready for :func:`ch_query`."""
        if not self._predicates:
            return "1=1", {}
        clauses = [p.clause for p in self._predicates]
        params: dict[str, Any] = {}
        for p in self._predicates:
            params.update(p.params)
        return " AND ".join(clauses), params
