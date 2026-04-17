from abc import abstractmethod
from datetime import datetime
from typing import TypedDict, Optional, Literal

from .equity_balance_listener import EquityBalanceListener
from .tracker_event_listener import TrackerEventListener


class TrackerUpdate(TypedDict, total=False):
    """Tracker configuration update."""

    name: str
    """Unique tracker name."""


Period = Literal[
    'day',
    'date',
    'week',
    'week-to-date',
    'month',
    'month-to-date',
    'quarter',
    'quarter-to-date',
    'year',
    'year-to-date',
    'lifetime',
]
"""Period length to track profit and drawdown for."""

ExceededThresholdType = Literal['profit', 'drawdown']
"""Type of the exceeded threshold."""


class NewTracker(TrackerUpdate, total=False):
    """New tracker configuration."""

    startBrokerTime: Optional[str]
    """Time to start tracking from in broker timezone, YYYY-MM-DD HH:mm:ss.SSS format."""
    endBrokerTime: Optional[str]
    """Time to end tracking at in broker timezone, YYYY-MM-DD HH:mm:ss.SSS format."""
    period: Period
    """Period length to track drawdown for."""
    relativeDrawdownThreshold: Optional[float]
    """Relative drawdown threshold after which tracker event is generated, a fraction of 1."""
    absoluteDrawdownThreshold: Optional[float]
    """Absolute drawdown threshold after which tracker event is generated, should be greater than 0."""
    relativeProfitThreshold: Optional[float]
    """Relative profit threshold after which tracker event is generated, a fraction of 1."""
    absoluteProfitThreshold: Optional[float]
    """Absolute profit threshold after which tracker event is generated, should be greater than 0."""


class TrackerId(TypedDict):
    """Tracker id."""

    id: str
    """Unique tracker id."""


class Tracker(NewTracker):
    """Tracker configuration."""

    _id: str
    """Unique tracker id."""


class TrackerEvent(TypedDict, total=False):
    """Profit/drawdown threshold exceeded event model."""

    sequenceNumber: int
    """Event unique sequence number."""
    accountId: str
    """MetaApi account id."""
    trackerId: str
    """Profit/drawdown tracker id."""
    startBrokerTime: str
    """Profit/drawdown tracking period start time in broker timezone, in YYYY-MM-DD HH:mm:ss.SSS format."""
    endBrokerTime: Optional[str]
    """Profit/drawdown tracking period end time in broker timezone, in YYYY-MM-DD HH:mm:ss.SSS format."""
    period: Period
    """Profit/drawdown tracking period."""
    brokerTime: str
    """Profit/drawdown threshold exceeded event time in broker timezone, in YYY-MM-DD HH:mm:ss.SSS format."""
    absoluteDrawdown: float
    """Absolute drawdown value which was observed when the profit or drawdown threshold was exceeded."""
    relativeDrawdown: float
    """Relative drawdown value which was observed when the profit or drawdown threshold was exceeded."""
    absoluteProfit: float
    """Absolute profit value which was observed when the profit or drawdown threshold was exceeded."""
    relativeProfit: float
    """Relative profit value which was observed when the profit or drawdown threshold was exceeded."""
    exceededThresholdType: ExceededThresholdType
    """Type of the exceeded threshold."""


class PeriodStatistics(TypedDict, total=False):
    """Period statistics."""

    startBrokerTime: str
    """Period start time in broker timezone, in YYYY-MM-DD HH:mm:ss format."""
    endBrokerTime: Optional[str]
    """Period end time in broker timezone, in YYYY-MM-DD HH:mm:ss format."""
    period: Period
    """Period length."""
    initialBalance: float
    """Balance at period start time."""
    maxDrawdownTime: Optional[str]
    """Time max drawdown was observed at in broker timezone, in YYYY-MM-DD HH:mm:ss format"""
    maxAbsoluteDrawdown: Optional[float]
    """The value of maximum absolute drawdown observed."""
    maxRelativeDrawdown: Optional[float]
    """The value of maximum relative drawdown observed."""
    maxProfitTime: Optional[str]
    """Time max profit was observed at in broker timezone, in YYYY-MM-DD HH:mm:ss format"""
    maxAbsoluteProfit: Optional[float]
    """The value of maximum absolute profit observed."""
    maxRelativeProfit: Optional[float]
    """The value of maximum relative profit observed."""
    thresholdExceeded: bool
    """The flag indicating that max allowed total drawdown was exceeded."""
    exceededThresholdType: ExceededThresholdType
    """Type of the exceeded threshold."""
    balanceAdjustment: Optional[float]
    """Balance adjustment applied to equity for tracking drawdown/profit."""
    tradeDayCount: Optional[int]
    """Count of days when trades were performed during the period"""


class EquityChartItem(TypedDict):
    """Equity chart item."""

    startBrokerTime: str
    """Start time of a chart item as per broker timezone, in YYYY-MM-DD HH:mm:ss format."""
    endBrokerTime: str
    """End time of a chart item as per broker timezone, in YYYY-MM-DD HH:mm:ss format."""
    averageBalance: float
    """Average balance value during the period."""
    minBalance: float
    """Minimum balance value during the period."""
    maxBalance: float
    """Maximum balance value during the period."""
    averageEquity: float
    """Average equity value during the period."""
    minEquity: float
    """Minimum equity value during the period."""
    maxEquity: float
    """Maximum equity value during the period."""
    startBalance: float
    """Starting balance value observed during the period."""
    startEquity: float
    """Starting equity value observed during the period."""
    lastBalance: float
    """Last balance value observed during the period."""
    lastEquity: float
    """Last equity value observed during the period."""


class EquityTrackingClientModel:
    @abstractmethod
    async def create_tracker(self, account_id: str, tracker):
        """Creates a profit/drawdown tracker. See
        https://metaapi.cloud/docs/risk-management/restApi/api/createTracker/

        Args:
            account_id: ID of the MetaApi account.
            tracker: Profit/drawdown tracker.

        Returns:
            A coroutine resolving with profit/drawdown tracker id.
        """
        pass

    @abstractmethod
    async def get_trackers(self, account_id: str):
        """Returns trackers defined for an account. See
        https://metaapi.cloud/docs/risk-management/restApi/api/getTrackers/

        Args:
            account_id: ID of the MetaApi account.

        Returns:
            A coroutine resolving with trackers.
        """
        pass

    @abstractmethod
    async def get_tracker(self, account_id: str, id: str):
        """Returns profit/drawdown tracker by account and id. See
        https://metaapi.cloud/docs/risk-management/restApi/api/getTracker/

        Args:
            account_id: ID of the MetaApi account.
            id: Tracker ID.

        Returns:
            A coroutine resolving with profit/drawdown tracker found.
        """
        pass

    @abstractmethod
    async def get_tracker_by_name(self, account_id: str, name: str):
        """Returns profit/drawdown tracker by account and name

        Args:
            account_id: ID of the MetaApi account.
            name: Tracker name.

        Returns:
            A coroutine resolving with profit/drawdown tracker found.
        """
        pass

    @abstractmethod
    async def update_tracker(self, account_id: str, id: str, update):
        """Updates profit/drawdown tracker. See
        https://metaapi.cloud/docs/risk-management/restApi/api/updateTracker/

        Args:
            account_id: ID of the MetaApi account.
            id: ID of the tracker.
            update: Tracker update.

        Returns:
            A coroutine resolving when profit/drawdown tracker updated.
        """
        pass

    @abstractmethod
    async def delete_tracker(self, account_id: str, id: str):
        """Removes profit/drawdown tracker. See
        https://metaapi.cloud/docs/risk-management/restApi/api/removeTracker/

        Args:
            account_id: ID of the MetaApi account.
            id: ID of the tracker.

        Returns:
            A coroutine resolving when profit/drawdown tracker removed.
        """

    @abstractmethod
    async def get_tracker_events(
        self,
        start_broker_time: str = None,
        end_broker_time: str = None,
        account_id: str = None,
        tracker_id: str = None,
        limit: int = None,
    ):
        """Returns tracker events by broker time range. See
        https://metaapi.cloud/docs/risk-management/restApi/api/getTrackerEvents/

        Args:
            start_broker_time: Value of the event time in broker timezone to start loading data from, inclusive,
            in 'YYYY-MM-DD HH:mm:ss.SSS format.
            end_broker_time: Value of the event time in broker timezone to end loading data at, inclusive,
            in 'YYYY-MM-DD HH:mm:ss.SSS format.
            account_id: ID of the MetaApi account.
            tracker_id: ID of the tracker.
            limit: Pagination limit, default is 1000.

        Returns:
            A coroutine resolving with tracker events.
        """
        pass

    @abstractmethod
    def add_tracker_event_listener(
        self,
        listener: TrackerEventListener,
        account_id: str = None,
        tracker_id: str = None,
        sequence_number: int = None,
    ) -> str:
        """Adds a tracker event listener and creates a job to make requests.

        Args:
            listener: Tracker event listener.
            account_id: Account id.
            tracker_id: Tracker id.
            sequence_number: Sequence number.

        Returns:
            Listener id.
        """
        pass

    @abstractmethod
    def remove_tracker_event_listener(self, listener_id: str):
        """Removes tracker event listener and cancels the event stream

        Args:
            listener_id: Tracker event listener id.
        """
        pass

    @abstractmethod
    async def get_tracking_statistics(
        self, account_id: str, tracker_id: str, start_time: str = None, limit: int = None, real_time: bool = False
    ):
        """Returns account profit and drawdown tracking statistics by tracker id. See
        https://metaapi.cloud/docs/risk-management/restApi/api/getTrackingStats/

        Args:
            account_id: ID of MetaAPI account.
            tracker_id: ID of the tracker.
            start_time: Time to start loading stats from, default is current time. Note that stats is loaded in
            backwards direction.
            limit: Number of records to load, default is 1.
            real_time: If true, real-time data will be requested.

        Returns:
            A coroutine resolving with profit and drawdown statistics.
        """
        pass

    @abstractmethod
    async def add_period_statistics_listener(self, listener, account_id: str, tracker_id: str) -> str:
        """Adds a period statistics event listener.

        Args:
            listener: Period statistics event listener.
            account_id: Account ID.
            tracker_id: Tracker ID.

        Returns:
            Listener ID.
        """
        pass

    @abstractmethod
    def remove_period_statistics_listener(self, listener_id: str):
        """Removes period statistics event listener by id

        Args:
            listener_id: Listener id.
        """
        pass

    @abstractmethod
    async def get_equity_chart(
        self,
        account_id: str,
        start_time: str = None,
        end_time: str = None,
        real_time: bool = False,
        fill_skips: bool = False,
    ):
        """Returns equity chart by account id. See
        https://metaapi.cloud/docs/risk-management/restApi/api/getEquityChart/

        Args:
            account_id: MetaApi account id.
            start_time: Starting broker time in YYYY-MM-DD HH:mm:ss format.
            end_time: Ending broker time in YYYY-MM-DD HH:mm:ss format.
            real_time: If true, real-time data will be requested.
            fill_skips: If true, skipped records will be automatically filled based on existing ones.

        Returns:
            A coroutine resolving with equity chart.
        """
        pass

    @abstractmethod
    async def add_equity_chart_listener(self, listener, account_id: str, start_time: datetime) -> str:
        """Adds an equity chart event listener.

        Args:
            listener: Equity chart event listener.
            account_id: Account id.
            start_time: Date to start tracking from.

        Returns:
            Listener ID.
        """
        pass

    @abstractmethod
    def remove_equity_chart_listener(self, listener_id: str):
        """Removes equity chart event listener by id.

        Args:
            listener_id: Equity chart listener id.
        """
        pass

    @abstractmethod
    async def add_equity_balance_listener(self, listener: EquityBalanceListener, account_id: str) -> str:
        """Adds an equity balance event listener.

        Args:
            listener: Equity balance event listener.
            account_id: Account ID.

        Returns:
            Listener ID.
        """
        pass

    @abstractmethod
    def remove_equity_balance_listener(self, listener_id: str):
        """Removes equity balance event listener by ID.

        Args:
            listener_id: Equity balance listener ID.
        """
        pass
