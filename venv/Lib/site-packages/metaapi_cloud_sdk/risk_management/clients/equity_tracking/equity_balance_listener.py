from abc import abstractmethod
from typing import TypedDict


class EquityBalanceData(TypedDict, total=False):
    """Equity balance data for account."""

    equity: float
    """Account equity."""
    balance: float
    """Account balance."""


class EquityBalanceListener:
    """Equity balance event listener for handling a stream of equity and balance updates."""

    def __init__(self, account_id):
        """Creates an equity balance listener instance.

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
    async def on_equity_or_balance_updated(self, equity_balance_data: EquityBalanceData):
        """Processes an update event when equity or balance changes.

        Args:
            equity_balance_data: Equity and balance updated data.
        """
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
