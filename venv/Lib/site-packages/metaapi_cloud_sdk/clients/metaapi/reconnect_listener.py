from abc import ABC, abstractmethod


class ReconnectListener(ABC):
    """Defines interface for a websocket reconnect listener class."""

    @abstractmethod
    async def on_reconnected(self, region: str, instance_number: int):
        """Invoked when connection to MetaTrader terminal re-established.

        Args:
            region: Reconnected region.
            instance_number: Reconnected instance number.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        pass
