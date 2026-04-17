import asyncio
from datetime import datetime

from typing_extensions import TypedDict

from .http_client import HttpClient
from ..logger import LoggerManager
from ..metaapi.models import string_format_error


class DomainSettings(TypedDict, total=False):
    """Domain settings."""

    hostname: str
    """Client api host name"""
    domain: str
    """Client api domain for regions."""


class DomainClient:
    """Connection URL managing client."""

    def __init__(self, http_client: HttpClient, token: str, domain: str = 'agiliumtrade.agiliumtrade.ai'):
        """Constructs domain client instance.

        Args:
            http_client: HTTP client.
            token: Authorization token.
            domain: Domain to connect to, default is agiliumtrade.agiliumtrade.ai.
        """
        self._http_client = http_client
        self._domain = domain
        self._token = token
        self._url_cache = {'domain': None, 'hostname': None, 'requestPromise': None, 'lastUpdated': 0}
        self._retry_interval_in_seconds = 1
        self._logger = LoggerManager.get_logger('DomainClient')

    @property
    def domain(self) -> str:
        """Returns domain client domain.

        Returns:
            Client domain.
        """
        return self._domain

    @property
    def token(self) -> str:
        """Returns domain client token

        Returns:
            Client token.
        """
        return self._token

    async def get_url(self, host: str, region: str) -> str:
        """Returns the API URL.

        Args:
            host: REST API host.
            region: Host region.

        Returns:
            API URL.
        """
        await self._update_domain()
        return f'{host}.{region}.{self._url_cache["domain"]}'

    async def get_settings(self) -> DomainSettings:
        """Returns domain settings.

        Returns:
            Domain settings.
        """
        await self._update_domain()
        return {'domain': self._url_cache['domain'], 'hostname': self._url_cache['hostname']}

    async def _update_domain(self):
        if not self._url_cache['domain'] or self._url_cache['lastUpdated'] < datetime.now().timestamp() - 60 * 10:
            if self._url_cache['requestPromise']:
                await self._url_cache['requestPromise']
            else:
                future = asyncio.Future()
                self._url_cache['requestPromise'] = future

                is_cache_updated = False
                while not is_cache_updated:
                    opts = {
                        'url': f'https://mt-provisioning-api-v1.{self._domain}/users/current/servers/mt-client-api',
                        'method': 'GET',
                        'headers': {'auth-token': self._token},
                    }

                    try:
                        url_settings = await self._http_client.request(opts, '_update_domain')
                        self._url_cache = {
                            'domain': url_settings['domain'],
                            'hostname': url_settings['hostname'],
                            'requestPromise': None,
                            'lastUpdated': datetime.now().timestamp(),
                        }
                        future.set_result(True)
                        is_cache_updated = True
                        self._retry_interval_in_seconds = 1
                    except Exception as err:
                        self._logger.error('Failed to update domain settings cache', string_format_error(err))
                        self._retry_interval_in_seconds = min(self._retry_interval_in_seconds * 2, 300)
                        await asyncio.sleep(self._retry_interval_in_seconds)
