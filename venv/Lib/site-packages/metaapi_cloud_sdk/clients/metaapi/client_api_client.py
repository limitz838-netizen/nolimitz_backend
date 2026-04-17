import asyncio
from datetime import datetime
from typing import List

from typing_extensions import TypedDict

from ..domain_client import DomainClient
from ..error_handler import NotFoundException
from ..http_client import HttpClient
from ..metaapi_client import MetaApiClient
from ...logger import LoggerManager
from ...metaapi.models import format_error


class TypeHashingIgnoredFieldLists(TypedDict):
    """Type hashing ignored field lists"""

    specification: List[str]
    """Specification ignored fields."""
    position: List[str]
    """Position ignored fields."""
    order: List[str]
    """Order ignored fields."""


class HashingIgnoredFieldLists(TypedDict):
    """Hashing ignored field lists."""

    g1: TypeHashingIgnoredFieldLists
    """G1 hashing ignored field lists."""
    g2: TypeHashingIgnoredFieldLists
    """G2 hashing ignored field lists."""


class ClientApiClient(MetaApiClient):
    """metaapi.cloud client API client (see https://metaapi.cloud/docs/client/)"""

    def __init__(self, http_client: HttpClient, domain_client: DomainClient):
        """Initializes client API client instance.

        Args:
            http_client: HTTP client.
            domain_client: Domain client.
        """
        super().__init__(http_client, domain_client)
        self._host = f'https://mt-client-api-v1'
        self._retry_interval_in_seconds = 1
        self._update_interval = 60 * 60
        self._ignored_field_lists_caches = {}
        self._ignored_field_lists_freshest_cache = None
        self._logger = LoggerManager.get_logger('ClientApiClient')

    async def refresh_ignored_field_lists(self, region):
        if self._ignored_field_lists_caches.get(region) and self._ignored_field_lists_caches[region].get(
            'requestPromise'
        ):
            await self._ignored_field_lists_caches[region]['requestPromise']
        elif (
            self._ignored_field_lists_caches.get(region)
            and datetime.now().timestamp() - self._ignored_field_lists_caches[region].get('lastUpdated')
            < self._update_interval
        ):
            return
        else:
            if not self._ignored_field_lists_caches.get(region):

                async def update_job():
                    while True:
                        await asyncio.sleep(60)
                        asyncio.create_task(self._refresh_ignored_field_lists_job(region))

                self._ignored_field_lists_caches[region] = {
                    'lastUpdated': 0,
                    'data': None,
                    'requestPromise': None,
                    'updateJob': asyncio.create_task(update_job()),
                }
            future = asyncio.Future()
            self._ignored_field_lists_caches[region]['requestPromise'] = future
            is_cache_updated = False
            while not is_cache_updated:
                try:
                    host = await self._domain_client.get_url(self._host, region)
                    opts = {
                        "url": f"{host}/hashing-ignored-field-lists",
                        "method": "GET",
                        "headers": {"auth-token": self._token},
                    }
                    response = await self._http_client.request(opts, 'get_hashing_ignored_field_lists')
                    self._ignored_field_lists_caches[region] = {
                        'lastUpdated': datetime.now().timestamp(),
                        'data': response,
                        'requestPromise': None,
                    }
                    self._ignored_field_lists_freshest_cache = response
                    future.set_result(response)
                    is_cache_updated = True
                    self._ignored_field_lists_caches[region]['retryIntervalInSeconds'] = self._retry_interval_in_seconds
                except Exception as err:
                    self._logger.error(f'Failed to update hashing ignored field list {format_error(err)}')
                    self._ignored_field_lists_caches[region]['retryIntervalInSeconds'] = min(
                        self._ignored_field_lists_caches[region].get('retryIntervalInSeconds', 0) * 2, 300
                    )
                    await asyncio.sleep(self._ignored_field_lists_caches[region]['retryIntervalInSeconds'])

    def get_hashing_ignored_field_lists(self, region: str) -> HashingIgnoredFieldLists:
        """Retrieves hashing ignored field lists.

        Args:
            region: Account region.

        Returns:
            Hashing ignored field lists.
        """
        if region == "combined":
            if self._ignored_field_lists_freshest_cache:
                return self._ignored_field_lists_freshest_cache
            else:
                raise NotFoundException('Ignored field lists not found.')

        if (
            self._ignored_field_lists_caches.get(region)
            and region in self._ignored_field_lists_caches
            and self._ignored_field_lists_caches[region].get('data')
        ):
            return self._ignored_field_lists_caches[region]['data']
        else:
            raise NotFoundException(f"Ignored field lists for region {region} not found.")

    async def _refresh_ignored_field_lists_job(self, region: str):
        if (
            not self._ignored_field_lists_caches[region].get('requestPromise')
            and datetime.now().timestamp() - self._ignored_field_lists_caches[region]['lastUpdated']
            > self._update_interval
        ):
            await self.refresh_ignored_field_lists(region)
