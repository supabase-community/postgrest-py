from __future__ import annotations

from typing import Optional

from httpx import Headers, QueryParams
from pydantic import ValidationError

from ..base_request_builder import (
    APIResponse,
    BaseFilterRequestBuilder,
    BaseSelectRequestBuilder,
    CountMethod,
    pre_delete,
    pre_insert,
    pre_select,
    pre_update,
    pre_upsert,
)
from ..exceptions import APIError
from ..types import ReturnMethod
from ..utils import SyncClient


class SyncQueryRequestBuilder:
    def __init__(
        self,
        session: SyncClient,
        path: str,
        http_method: str,
        headers: Headers,
        params: QueryParams,
        json: dict,
    ) -> None:
        self.session = session
        self.path = path
        self.http_method = http_method
        self.headers = headers
        self.params = params
        self.json = json

    def execute(self) -> APIResponse:
        """Execute the query.

        .. tip::
            This is the last method called, after the query is built.

        Returns:
            :class:`APIResponse`

        Raises:
            :class:`APIError` If the API raised an error.
        """
        r = self.session.request(
            self.http_method,
            self.path,
            json=self.json,
            params=self.params,
            headers=self.headers,
        )

        try:
            if 200 <= r.status_code <= 299:  # Response.ok from JS (https://developer.mozilla.org/en-US/docs/Web/API/Response/ok)
                return APIResponse.from_http_request_response(r)
            else:
                raise APIError(r.json())
        except ValidationError as e:
            raise APIError(r.json()) from e


class SyncMaybeSingleRequestBuilder(SyncQueryRequestBuilder):
    def execute(self) -> APIResponse:
        try:
            r = super().execute()
        except APIError as e:
            if e.details and "Results contain 0 rows" in e.details:
                return APIResponse.from_dict({
                    "data": None,
                    "error": None,
                    "count": 0  # NOTE: needs to take value from res.count
                })
        return r


class SyncQueryBuilder:
    def __init__(
        self,
        session: SyncClient,
        path: str,
        http_method: str,
        headers: Headers,
        params: QueryParams,
        json: dict,
    ) -> None:
        self.session = session
        self.path = path
        self.http_method = http_method
        self.headers = headers
        self.params = params
        self.json = json

    @property
    def is_single(self):
        return self.headers["Accept"] == "application/vnd.pgrst.object+json"

    @property
    def is_maybe_single(self):
        cond = "x-maybeSingle" in self.headers and self.headers["x-maybeSingle"].lower() == "true"
        return self.is_single and cond

    @property
    def QueryRequestBuilder(self):
        return SyncQueryRequestBuilder(
            headers=self.headers,
            http_method=self.http_method,
            json=self.json,
            params=self.params,
            path=self.path,
            session=self.session,
        )

    @property
    def MaybeSingleRequestBuilder(self):
        return SyncMaybeSingleRequestBuilder(
            headers=self.headers,
            http_method=self.http_method,
            json=self.json,
            params=self.params,
            path=self.path,
            session=self.session,
        )

    def execute(self) -> APIResponse:
        if self.is_maybe_single:
            return self.MaybeSingleRequestBuilder.execute()
        else:
            return self.QueryRequestBuilder.execute()


# ignoring type checking as a workaround for https://github.com/python/mypy/issues/9319
class SyncFilterRequestBuilder(BaseFilterRequestBuilder, SyncQueryBuilder):  # type: ignore
    def __init__(
        self,
        session: SyncClient,
        path: str,
        http_method: str,
        headers: Headers,
        params: QueryParams,
        json: dict,
    ) -> None:
        BaseFilterRequestBuilder.__init__(self, session, headers, params)
        SyncQueryBuilder.__init__(
            self, session, path, http_method, headers, params, json
        )


# ignoring type checking as a workaround for https://github.com/python/mypy/issues/9319
class SyncSelectRequestBuilder(BaseSelectRequestBuilder, SyncQueryBuilder):  # type: ignore
    def __init__(
        self,
        session: SyncClient,
        path: str,
        http_method: str,
        headers: Headers,
        params: QueryParams,
        json: dict,
    ) -> None:
        BaseSelectRequestBuilder.__init__(self, session, headers, params)
        SyncQueryBuilder.__init__(
            self, session, path, http_method, headers, params, json
        )


class SyncRequestBuilder:
    def __init__(self, session: SyncClient, path: str) -> None:
        self.session = session
        self.path = path

    def select(
        self,
        *columns: str,
        count: Optional[CountMethod] = None,
    ) -> SyncSelectRequestBuilder:
        """Run a SELECT query.

        Args:
            *columns: The names of the columns to fetch.
            count: The method to use to get the count of rows returned.
        Returns:
            :class:`SyncSelectRequestBuilder`
        """
        method, params, headers, json = pre_select(*columns, count=count)
        return SyncSelectRequestBuilder(
            self.session, self.path, method, headers, params, json
        )

    def insert(
        self,
        json: dict,
        *,
        count: Optional[CountMethod] = None,
        returning: ReturnMethod = ReturnMethod.representation,
        upsert: bool = False,
    ) -> SyncQueryBuilder:
        """Run an INSERT query.

        Args:
            json: The row to be inserted.
            count: The method to use to get the count of rows returned.
            returning: Either 'minimal' or 'representation'
            upsert: Whether the query should be an upsert.
        Returns:
            :class:`SyncQueryBuilder`
        """
        method, params, headers, json = pre_insert(
            json,
            count=count,
            returning=returning,
            upsert=upsert,
        )
        return SyncQueryBuilder(
            self.session, self.path, method, headers, params, json
        )

    def upsert(
        self,
        json: dict,
        *,
        count: Optional[CountMethod] = None,
        returning: ReturnMethod = ReturnMethod.representation,
        ignore_duplicates: bool = False,
    ) -> SyncQueryBuilder:
        """Run an upsert (INSERT ... ON CONFLICT DO UPDATE) query.

        Args:
            json: The row to be inserted.
            count: The method to use to get the count of rows returned.
            returning: Either 'minimal' or 'representation'
            ignore_duplicates: Whether duplicate rows should be ignored.
        Returns:
            :class:`SyncQueryBuilder`
        """
        method, params, headers, json = pre_upsert(
            json,
            count=count,
            returning=returning,
            ignore_duplicates=ignore_duplicates,
        )
        return SyncQueryBuilder(
            self.session, self.path, method, headers, params, json
        )

    def update(
        self,
        json: dict,
        *,
        count: Optional[CountMethod] = None,
        returning: ReturnMethod = ReturnMethod.representation,
    ) -> SyncFilterRequestBuilder:
        """Run an UPDATE query.

        Args:
            json: The updated fields.
            count: The method to use to get the count of rows returned.
            returning: Either 'minimal' or 'representation'
        Returns:
            :class:`SyncFilterRequestBuilder`
        """
        method, params, headers, json = pre_update(
            json,
            count=count,
            returning=returning,
        )
        return SyncFilterRequestBuilder(
            self.session, self.path, method, headers, params, json
        )

    def delete(
        self,
        *,
        count: Optional[CountMethod] = None,
        returning: ReturnMethod = ReturnMethod.representation,
    ) -> SyncFilterRequestBuilder:
        """Run a DELETE query.

        Args:
            count: The method to use to get the count of rows returned.
            returning: Either 'minimal' or 'representation'
        Returns:
            :class:`SyncFilterRequestBuilder`
        """
        method, params, headers, json = pre_delete(
            count=count,
            returning=returning,
        )
        return SyncFilterRequestBuilder(
            self.session, self.path, method, headers, params, json
        )
