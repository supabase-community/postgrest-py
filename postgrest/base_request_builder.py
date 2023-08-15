from __future__ import annotations

import json
from json import JSONDecodeError
from re import search
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
)

from httpx import Headers, QueryParams
from httpx import Response as RequestResponse
from pydantic import BaseModel, validator

from .types import CountMethod, Filters, RequestMethod, ReturnMethod
from .utils import AsyncClient, SyncClient, sanitize_param


class QueryArgs(NamedTuple):
    # groups the method, json, headers and params for a query in a single object
    method: RequestMethod
    params: QueryParams
    headers: Headers
    json: Dict[Any, Any]


def pre_select(
    *columns: str,
    count: Optional[CountMethod] = None,
) -> QueryArgs:
    if columns:
        method = RequestMethod.GET
        params = QueryParams({"select": ",".join(columns)})
    else:
        method = RequestMethod.HEAD
        params = QueryParams()
    headers = Headers({"Prefer": f"count={count}"}) if count else Headers()
    return QueryArgs(method, params, headers, {})


def pre_insert(
    json: dict,
    *,
    count: Optional[CountMethod],
    returning: ReturnMethod,
    upsert: bool,
) -> QueryArgs:
    prefer_headers = [f"return={returning}"]
    if count:
        prefer_headers.append(f"count={count}")
    if upsert:
        prefer_headers.append("resolution=merge-duplicates")
    headers = Headers({"Prefer": ",".join(prefer_headers)})
    return QueryArgs(RequestMethod.POST, QueryParams(), headers, json)


def pre_upsert(
    json: dict,
    *,
    count: Optional[CountMethod],
    returning: ReturnMethod,
    ignore_duplicates: bool,
    on_conflict: str = "",
) -> QueryArgs:
    query_params = {}
    prefer_headers = [f"return={returning}"]
    if count:
        prefer_headers.append(f"count={count}")
    resolution = "ignore" if ignore_duplicates else "merge"
    prefer_headers.append(f"resolution={resolution}-duplicates")
    headers = Headers({"Prefer": ",".join(prefer_headers)})
    if on_conflict:
        query_params["on_conflict"] = on_conflict
    return QueryArgs(RequestMethod.POST, QueryParams(query_params), headers, json)


def pre_update(
    json: dict,
    *,
    count: Optional[CountMethod],
    returning: ReturnMethod,
) -> QueryArgs:
    prefer_headers = [f"return={returning}"]
    if count:
        prefer_headers.append(f"count={count}")
    headers = Headers({"Prefer": ",".join(prefer_headers)})
    return QueryArgs(RequestMethod.PATCH, QueryParams(), headers, json)


def pre_delete(
    *,
    count: Optional[CountMethod],
    returning: ReturnMethod,
) -> QueryArgs:
    prefer_headers = [f"return={returning}"]
    if count:
        prefer_headers.append(f"count={count}")
    headers = Headers({"Prefer": ",".join(prefer_headers)})
    return QueryArgs(RequestMethod.DELETE, QueryParams(), headers, {})


class APIResponse(BaseModel):
    data: List[Dict[str, Any]]
    """The data returned by the query."""
    count: Optional[int] = None
    """The number of rows returned."""

    @validator("data")
    @classmethod
    def raise_when_api_error(cls: Type[APIResponse], value: Any) -> Any:
        if isinstance(value, dict) and value.get("message"):
            raise ValueError("You are passing an API error to the data field.")
        return value

    @staticmethod
    def _get_count_from_content_range_header(
        content_range_header: str,
    ) -> Optional[int]:
        content_range = content_range_header.split("/")
        return None if len(content_range) < 2 else int(content_range[1])

    @staticmethod
    def _is_count_in_prefer_header(prefer_header: str) -> bool:
        pattern = f"count=({'|'.join([cm.value for cm in CountMethod])})"
        return bool(search(pattern, prefer_header))

    @classmethod
    def _get_count_from_http_request_response(
        cls: Type[APIResponse],
        request_response: RequestResponse,
    ) -> Optional[int]:
        prefer_header: Optional[str] = request_response.request.headers.get("prefer")
        if not prefer_header:
            return None
        is_count_in_prefer_header = cls._is_count_in_prefer_header(prefer_header)
        content_range_header: Optional[str] = request_response.headers.get(
            "content-range"
        )
        return (
            cls._get_count_from_content_range_header(content_range_header)
            if (is_count_in_prefer_header and content_range_header)
            else None
        )

    @classmethod
    def from_http_request_response(
        cls: Type[APIResponse], request_response: RequestResponse
    ) -> APIResponse:
        try:
            data = request_response.json()
        except JSONDecodeError as e:
            return cls(data=[], count=0)
        count = cls._get_count_from_http_request_response(request_response)
        return cls(data=data, count=count)

    @classmethod
    def from_dict(cls: Type[APIResponse], dict: Dict[str, Any]) -> APIResponse:
        keys = dict.keys()
        assert len(keys) == 3 and "data" in keys and "count" in keys and "error" in keys
        return cls(
            data=dict.get("data"), count=dict.get("count"), error=dict.get("error")
        )


class SingleAPIResponse(APIResponse):
    data: Dict[str, Any]  # type: ignore
    """The data returned by the query."""

    @classmethod
    def from_http_request_response(
        cls: Type[SingleAPIResponse], request_response: RequestResponse
    ) -> SingleAPIResponse:
        data = request_response.json()
        count = cls._get_count_from_http_request_response(request_response)
        return cls(data=data, count=count)

    @classmethod
    def from_dict(
        cls: Type[SingleAPIResponse], dict: Dict[str, Any]
    ) -> SingleAPIResponse:
        keys = dict.keys()
        assert len(keys) == 3 and "data" in keys and "count" in keys and "error" in keys
        return cls(
            data=dict.get("data"), count=dict.get("count"), error=dict.get("error")
        )


_FilterT = TypeVar("_FilterT", bound="BaseFilterRequestBuilder")


class BaseFilterRequestBuilder:
    def __init__(
        self,
        session: Union[AsyncClient, SyncClient],
        headers: Headers,
        params: QueryParams,
    ) -> None:
        self.session = session
        self.headers = headers
        self.params = params
        self.negate_next = False

    @property
    def not_(self: _FilterT) -> _FilterT:
        """Whether the filter applied next should be negated."""
        self.negate_next = True
        return self

    def filter(self: _FilterT, column: str, operator: str, criteria: str) -> _FilterT:
        """Apply filters on a query.

        Args:
            column: The name of the column to apply a filter on
            operator: The operator to use while filtering
            criteria: The value to filter by
        """
        if self.negate_next is True:
            self.negate_next = False
            operator = f"{Filters.NOT}.{operator}"
        key, val = sanitize_param(column), f"{operator}.{criteria}"
        self.params = self.params.add(key, val)
        return self

    def eq(self: _FilterT, column: str, value: Any) -> _FilterT:
        """An 'equal to' filter.

        Args:
            column: The name of the column to apply a filter on
            value: The value to filter by
        """
        return self.filter(column, Filters.EQ, value)

    def neq(self: _FilterT, column: str, value: Any) -> _FilterT:
        """A 'not equal to' filter

        Args:
            column: The name of the column to apply a filter on
            value: The value to filter by
        """
        return self.filter(column, Filters.NEQ, value)

    def gt(self: _FilterT, column: str, value: Any) -> _FilterT:
        """A 'greater than' filter

        Args:
            column: The name of the column to apply a filter on
            value: The value to filter by
        """
        return self.filter(column, Filters.GT, value)

    def gte(self: _FilterT, column: str, value: Any) -> _FilterT:
        """A 'greater than or equal to' filter

        Args:
            column: The name of the column to apply a filter on
            value: The value to filter by
        """
        return self.filter(column, Filters.GTE, value)

    def lt(self: _FilterT, column: str, value: Any) -> _FilterT:
        """A 'less than' filter

        Args:
            column: The name of the column to apply a filter on
            value: The value to filter by
        """
        return self.filter(column, Filters.LT, value)

    def lte(self: _FilterT, column: str, value: Any) -> _FilterT:
        """A 'less than or equal to' filter

        Args:
            column: The name of the column to apply a filter on
            value: The value to filter by
        """
        return self.filter(column, Filters.LTE, value)

    def is_(self: _FilterT, column: str, value: Any) -> _FilterT:
        """An 'is' filter

        Args:
            column: The name of the column to apply a filter on
            value: The value to filter by
        """
        return self.filter(column, Filters.IS, value)

    def like(self: _FilterT, column: str, pattern: str) -> _FilterT:
        """A 'LIKE' filter, to use for pattern matching.

        Args:
            column: The name of the column to apply a filter on
            pattern: The pattern to filter by
        """
        return self.filter(column, Filters.LIKE, pattern)

    def ilike(self: _FilterT, column: str, pattern: str) -> _FilterT:
        """An 'ILIKE' filter, to use for pattern matching (case insensitive).

        Args:
            column: The name of the column to apply a filter on
            pattern: The pattern to filter by
        """
        return self.filter(column, Filters.ILIKE, pattern)

    def fts(self: _FilterT, column: str, query: Any) -> _FilterT:
        return self.filter(column, Filters.FTS, query)

    def plfts(self: _FilterT, column: str, query: Any) -> _FilterT:
        return self.filter(column, Filters.PLFTS, query)

    def phfts(self: _FilterT, column: str, query: Any) -> _FilterT:
        return self.filter(column, Filters.PHFTS, query)

    def wfts(self: _FilterT, column: str, query: Any) -> _FilterT:
        return self.filter(column, Filters.WFTS, query)

    def in_(self: _FilterT, column: str, values: Iterable[Any]) -> _FilterT:
        values = map(sanitize_param, values)
        values = ",".join(values)
        return self.filter(column, Filters.IN, f"({values})")

    def cs(self: _FilterT, column: str, values: Iterable[Any]) -> _FilterT:
        values = ",".join(values)
        return self.filter(column, Filters.CS, f"{{{values}}}")

    def cd(self: _FilterT, column: str, values: Iterable[Any]) -> _FilterT:
        values = ",".join(values)
        return self.filter(column, Filters.CD, f"{{{values}}}")

    def contains(
        self: _FilterT, column: str, value: Union[Iterable[Any], str, Dict[Any, Any]]
    ) -> _FilterT:
        if isinstance(value, str):
            # range types can be inclusive '[', ']' or exclusive '(', ')' so just
            # keep it simple and accept a string
            return self.filter(column, Filters.CS, value)
        if not isinstance(value, dict) and isinstance(value, Iterable):
            # Expected to be some type of iterable
            stringified_values = ",".join(value)
            return self.filter(column, Filters.CS, f"{{{stringified_values}}}")

        return self.filter(column, Filters.CS, json.dumps(value))

    def contained_by(
        self: _FilterT, column: str, value: Union[Iterable[Any], str, Dict[Any, Any]]
    ) -> _FilterT:
        if isinstance(value, str):
            # range
            return self.filter(column, Filters.CD, value)
        if not isinstance(value, dict) and isinstance(value, Iterable):
            stringified_values = ",".join(value)
            return self.filter(column, Filters.CD, f"{{{stringified_values}}}")
        return self.filter(column, Filters.CD, json.dumps(value))

    def ov(self: _FilterT, column: str, values: Iterable[Any]) -> _FilterT:
        values = ",".join(values)
        return self.filter(column, Filters.OV, f"{{{values}}}")

    def sl(self: _FilterT, column: str, range: Tuple[int, int]) -> _FilterT:
        return self.filter(column, Filters.SL, f"({range[0]},{range[1]})")

    def sr(self: _FilterT, column: str, range: Tuple[int, int]) -> _FilterT:
        return self.filter(column, Filters.SR, f"({range[0]},{range[1]})")

    def nxl(self: _FilterT, column: str, range: Tuple[int, int]) -> _FilterT:
        return self.filter(column, Filters.NXL, f"({range[0]},{range[1]})")

    def nxr(self: _FilterT, column: str, range: Tuple[int, int]) -> _FilterT:
        return self.filter(column, Filters.NXR, f"({range[0]},{range[1]})")

    def adj(self: _FilterT, column: str, range: Tuple[int, int]) -> _FilterT:
        return self.filter(column, Filters.ADJ, f"({range[0]},{range[1]})")

    def match(self: _FilterT, query: Dict[str, Any]) -> _FilterT:
        updated_query = self

        if not query:
            raise ValueError(
                "query dictionary should contain at least one key-value pair"
            )

        for key, value in query.items():
            updated_query = self.eq(key, value)

        return updated_query


class BaseSelectRequestBuilder(BaseFilterRequestBuilder):
    def __init__(
        self,
        session: Union[AsyncClient, SyncClient],
        headers: Headers,
        params: QueryParams,
    ) -> None:
        BaseFilterRequestBuilder.__init__(self, session, headers, params)

    def explain(
        self: _FilterT,
        analyze: bool = False,
        verbose: bool = False,
        settings: bool = False,
        buffers: bool = False,
        wal: bool = False,
        format: str = "",
    ) -> _FilterT:
        options = [
            key
            for key, value in locals().items()
            if key not in ["self", "format"] and value
        ]
        options_str = "|".join(options)
        options_str = f'options="{options_str};"' if options_str else ""
        self.headers["Accept"] = f"application/vnd.pgrst.plan+{format}; {options_str}"
        return self

    def order(
        self: _FilterT,
        column: str,
        *,
        desc: bool = False,
        nullsfirst: bool = False,
        foreign_table: Optional[str] = None,
    ) -> _FilterT:
        """Sort the returned rows in some specific order.

        Args:
            column: The column to order by
            desc: Whether the rows should be ordered in descending order or not.
            nullsfirst: nullsfirst
            foreign_table: Foreign table name whose results are to be ordered.
        .. versionchanged:: 0.10.3
           Allow ordering results for foreign tables with the foreign_table parameter.
        """
        self.params = self.params.add(
            f"{foreign_table}.order" if foreign_table else "order",
            f"{column}{'.desc' if desc else ''}{'.nullsfirst' if nullsfirst else ''}",
        )
        return self

    def limit(
        self: _FilterT, size: int, *, foreign_table: Optional[str] = None
    ) -> _FilterT:
        """Limit the number of rows returned by a query.

        Args:
            size: The number of rows to be returned
            foreign_table: Foreign table name to limit
        .. versionchanged:: 0.10.3
           Allow limiting results returned for foreign tables with the foreign_table parameter.
        """
        self.params = self.params.add(
            f"{foreign_table}.limit" if foreign_table else "limit",
            size,
        )
        return self

    def range(self: _FilterT, start: int, end: int) -> _FilterT:
        self.headers["Range-Unit"] = "items"
        self.headers["Range"] = f"{start}-{end - 1}"
        return self
