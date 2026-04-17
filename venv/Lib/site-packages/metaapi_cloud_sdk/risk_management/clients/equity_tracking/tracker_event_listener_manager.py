import asyncio

from .tracker_event_listener import TrackerEventListener
from ..domain_client import DomainClient
from ...models import random_id


class TrackerEventListenerManager:
    """Manager for handling tracking event listeners."""

    def __init__(self, domain_client: DomainClient):
        """Initializes tracker event listener manager instance.

        Args:
            domain_client: Domain client.
        """
        self._domain_client = domain_client
        self._tracker_event_listeners = {}
        self._error_throttle_time = 1

    @property
    def tracker_event_listeners(self):
        """Returns the dictionary of tracker event listeners.

        Returns:
            Dictionary of tracker event listeners.
        """
        return self._tracker_event_listeners

    def add_tracker_event_listener(
        self,
        listener: TrackerEventListener,
        account_id: str = None,
        tracker_id: str = None,
        sequence_number: int = None,
    ) -> str:
        """Adds a tracker event listener.

        Args:
            listener: Tracker event listener.
            account_id: Account id.
            tracker_id: Tracker id.
            sequence_number: Event sequence number.

        Returns:
            Tracker event listener id.
        """
        listener_id = random_id(10)
        self._tracker_event_listeners[listener_id] = listener
        asyncio.create_task(
            self._start_tracker_event_job(listener_id, listener, account_id, tracker_id, sequence_number)
        )
        return listener_id

    def remove_tracker_event_listener(self, listener_id: str):
        """Removes tracker event listener by id.

        Args:
            listener_id: Listener id.
        """
        if listener_id in self._tracker_event_listeners:
            del self._tracker_event_listeners[listener_id]

    async def _start_tracker_event_job(
        self,
        listener_id: str,
        listener: TrackerEventListener,
        account_id: str = None,
        tracker_id: str = None,
        sequence_number: int = None,
    ):
        throttle_time = self._error_throttle_time
        while listener_id in self._tracker_event_listeners:
            opts = {
                'url': '/users/current/tracker-events/stream',
                'method': 'GET',
                'headers': {'auth-token': self._domain_client.token, 'api-version': '1'},
                'qs': {
                    'previousSequenceNumber': sequence_number,
                    'accountId': account_id,
                    'trackerId': tracker_id,
                    'limit': 1000,
                },
            }
            try:
                packets = await self._domain_client.request_api(opts, True)
                for packet in packets:
                    await listener.on_tracker_event(packet)
                throttle_time = self._error_throttle_time
                if listener_id in self._tracker_event_listeners and len(packets):
                    sequence_number = packets[-1]['sequenceNumber']
            except Exception as err:
                asyncio.create_task(listener.on_error(err))
                await asyncio.sleep(throttle_time)
                throttle_time = min(throttle_time * 2, 30)
