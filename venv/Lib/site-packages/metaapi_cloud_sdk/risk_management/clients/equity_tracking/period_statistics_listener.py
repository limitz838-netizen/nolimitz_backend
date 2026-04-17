from abc import abstractmethod
from typing import List

from .equity_tracking_client_model import PeriodStatistics


class PeriodStatisticsListener:
    """Period statistics event listener for handling a stream of period statistics events."""

    def __init__(self, account_id: str, tracker_id: str):
        """Creates a tracker event listener instance.

        Args:
            account_id: Account id.
            tracker_id: Tracker id.
        """
        if not account_id:
            raise Exception('Account id parameter required')

        if not tracker_id:
            raise Exception('Tracker id parameter required')

        self._account_id = account_id
        self._tracker_id = tracker_id

    @property
    def account_id(self):
        """Returns account id."""
        return self._account_id

    @property
    def tracker_id(self):
        """Returns tracker id."""
        return self._tracker_id

    @abstractmethod
    async def on_period_statistics_updated(self, period_statistics_event: List[PeriodStatistics]):
        """Processes period statistics event which occurs when new period statistics data arrives.

        Args:
            period_statistics_event: Period statistics event.
        """
        pass

    @abstractmethod
    async def on_period_statistics_completed(self):
        """Processes period statistics event which occurs when a statistics period ends."""
        pass

    @abstractmethod
    async def on_tracker_completed(self):
        """Processes period statistics event which occurs when the tracker period ends."""
        pass

    @abstractmethod
    async def on_connected(self):
        """Processes an event which occurs when connection has been established."""
        pass

    @abstractmethod
    async def on_disconnected(self):
        """Processes an event which occurs when connection has been lost."""
        pass

    async def on_error(self, error: Exception):
        """Processes an error event.

        Args:
            error: Error received.
        """
        pass
