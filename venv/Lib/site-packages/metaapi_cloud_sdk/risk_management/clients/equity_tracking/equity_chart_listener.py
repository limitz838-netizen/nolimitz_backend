from abc import abstractmethod
from typing import List

from .equity_tracking_client_model import EquityChartItem


class EquityChartListener:
    """Equity chart event listener for handling a stream of equity chart events."""

    def __init__(self, account_id):
        """Creates an equity chart listener instance.

        Args:
            account_id: Account id.
        """
        if not account_id:
            raise Exception('Account id parameter required')
        self._account_id = account_id

    @property
    def account_id(self) -> str:
        """Returns account id."""
        return self._account_id

    @abstractmethod
    async def on_equity_record_updated(self, equity_chart_event: List[EquityChartItem]):
        """Processes equity chart event which occurs when new equity chart data arrives.

        Args:
            equity_chart_event: Equity chart event.
        """
        pass

    @abstractmethod
    async def on_equity_record_completed(self):
        """Processes equity chart event which occurs when an equity chart period ends."""
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
