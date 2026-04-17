import asyncio

import math

from .stopout_listener import StopoutListener
from ...domain_client import DomainClient
from ...metaapi_client import MetaApiClient
from ....logger import LoggerManager
from ....models import random_id


class StopoutListenerManager(MetaApiClient):
    """Stopout event listener manager."""

    def __init__(self, domain_client: DomainClient):
        """Initializes stopout listener manager instance.

        Args:
            domain_client: Domain client.
        """
        super().__init__(domain_client)
        self._domain_client = domain_client
        self._stopout_listeners = {}
        self._error_throttle_time = 1
        self._logger = LoggerManager.get_logger('StopoutListenerManager')

    @property
    def stopout_listeners(self):
        """Returns the dictionary of stopout listeners.

        Returns:
            Dictionary of stopout listeners.
        """
        return self._stopout_listeners

    def add_stopout_listener(
        self, listener: StopoutListener, account_id: str = None, strategy_id: str = None, sequence_number: int = None
    ) -> str:
        """Adds a stopout listener.

        Args:
            listener: Stopout listener.
            account_id:  Account id.
            strategy_id: Strategy id.
            sequence_number: Event sequence number.

        Returns:
            Stopout listener id.
        """
        listener_id = random_id(10)
        self._stopout_listeners[listener_id] = listener
        asyncio.create_task(
            self._start_stopout_event_job(listener_id, listener, account_id, strategy_id, sequence_number)
        )
        return listener_id

    def remove_stopout_listener(self, listener_id: str):
        """Removes stopout listener by id.

        Args:
            listener_id: listener id.
        """
        if listener_id in self._stopout_listeners:
            del self._stopout_listeners[listener_id]

    async def _start_stopout_event_job(
        self,
        listener_id: str,
        listener: StopoutListener,
        account_id: str = None,
        strategy_id: str = None,
        sequence_number: int = None,
    ):
        throttle_time = self._error_throttle_time
        while listener_id in self._stopout_listeners:
            opts = {
                'url': '/users/current/stopouts/stream',
                'method': 'GET',
                'params': {
                    'previousSequenceNumber': sequence_number,
                    'subscriberId': account_id,
                    'strategyId': strategy_id,
                    'limit': 1000,
                },
                'headers': {'auth-token': self._token},
            }
            try:
                packets = await self._domain_client.request_copyfactory(opts, True)
                await listener.on_stopout(packets)
                throttle_time = self._error_throttle_time
                if listener_id in self._stopout_listeners and len(packets):
                    sequence_number = packets[-1]['sequenceNumber']
            except Exception as err:
                await listener.on_error(err)
                self._logger.error(
                    f'Failed to retrieve stopouts stream for strategy {strategy_id}, '
                    + f'listener {listener_id}, retrying in {math.floor(throttle_time)} seconds',
                    err,
                )
                await asyncio.sleep(throttle_time)
                throttle_time = min(throttle_time * 2, 30)
