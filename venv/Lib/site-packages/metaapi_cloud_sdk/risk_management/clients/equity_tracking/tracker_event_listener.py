from abc import abstractmethod


class TrackerEventListener:
    """Tracker event listener for handling a stream of profit/drawdown events."""

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
    async def on_tracker_event(self, tracker_event):
        """Processes profit/drawdown event which occurs when a profit/drawdown limit is exceeded in a tracker.

        Args:
            tracker_event: Profit/drawdown event.
        """
        pass

    async def on_error(self, error: Exception):
        """Processes an error event.

        Args:
            error: Error received.
        """
        pass
