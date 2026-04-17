from typing import List

from .copyfactory_models import (
    CopyFactoryTradingSignal
)
from ..domain_client import DomainClient
from ...models import convert_iso_time_to_date


class SubscriberSignalClient:
    """CopyFactory client for signal requests."""

    def __init__(self, account_id: str, host: dict, domain_client: DomainClient):
        """Initializes CopyFactory signal client instance.

        Args:
            account_id: Account id.
            host: Host data.
            domain_client: Domain client.
        """
        self._account_id = account_id
        self._domain_client = domain_client
        self._host = host

    async def get_trading_signals(self) -> 'List[CopyFactoryTradingSignal]':
        """Returns trading signals the subscriber is subscribed to. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/trading/getTradingSignals/

        Returns:
            A coroutine which resolves with signals found.
        """
        opts = {
            'url': f'/users/current/subscribers/{self._account_id}/signals',
            'method': 'GET',
            'headers': {'auth-token': self._domain_client.token},
        }
        result = await self._domain_client.request_signal(opts, self._host, self._account_id)
        convert_iso_time_to_date(result)
        return result
