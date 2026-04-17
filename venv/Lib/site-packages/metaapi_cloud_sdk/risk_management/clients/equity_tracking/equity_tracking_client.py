import json
from datetime import datetime, timedelta
from typing import List, Coroutine, Any
from urllib import parse

from .equity_balance_listener import EquityBalanceListener
from .equity_balance_stream_manager import EquityBalanceStreamManager
from .equity_chart_listener import EquityChartListener
from .equity_chart_stream_manager import EquityChartStreamManager
from .equity_tracking_client_model import (
    PeriodStatistics,
    EquityChartItem,
    NewTracker,
    Tracker,
    TrackerUpdate,
    TrackerEvent,
    EquityTrackingClientModel,
    TrackerId,
)
from .period_statistics_listener import PeriodStatisticsListener
from .period_statistics_stream_manager import PeriodStatisticsStreamManager
from .tracker_event_listener import TrackerEventListener
from .tracker_event_listener_manager import TrackerEventListenerManager
from ..domain_client import DomainClient
from ...models import date, format_date_broker
from .... import MetaApi


class EquityTrackingClient(EquityTrackingClientModel):
    """metaapi.cloud RiskManagement equity tracking API client (see https://metaapi.cloud/docs/riskManagement/)"""

    def __init__(self, domain_client: DomainClient, meta_api: MetaApi):
        """Initializes RiskManagement equity tracking API client instance.

        Args:
            domain_client: Domain client.
        """
        self._domain_client = domain_client
        self._tracker_event_listener_manager = TrackerEventListenerManager(domain_client)
        self._equity_balance_stream_manager = EquityBalanceStreamManager(domain_client, meta_api)
        self._period_statistics_stream_manager = PeriodStatisticsStreamManager(domain_client, self, meta_api)
        self._equity_chart_stream_manager = EquityChartStreamManager(domain_client, self, meta_api)

    async def create_tracker(self, account_id: str, tracker: NewTracker) -> TrackerId:
        """Creates a profit/drawdown tracker. See
        https://metaapi.cloud/docs/risk-management/restApi/api/createTracker/

        Args:
            account_id: ID of the MetaApi account.
            tracker: Profit/drawdown tracker.

        Returns:
            A coroutine resolving with profit/drawdown tracker id.
        """
        return await self._domain_client.request_api(
            {
                'url': f'/users/current/accounts/{account_id}/trackers',
                'headers': {'auth-token': self._domain_client.token, 'api-version': '1'},
                'method': 'POST',
                'body': tracker,
            }
        )

    async def get_trackers(self, account_id: str) -> List[Tracker]:
        """Returns trackers defined for an account. See
        https://metaapi.cloud/docs/risk-management/restApi/api/getTrackers/

        Args:
            account_id: ID of the MetaApi account.

        Returns:
            A coroutine resolving with trackers.
        """
        return await self._domain_client.request_api(
            {
                'url': f'/users/current/accounts/{account_id}/trackers',
                'headers': {'auth-token': self._domain_client.token, 'api-version': '1'},
                'method': 'GET',
            }
        )

    async def get_tracker(self, account_id: str, id: str) -> Tracker:
        """Returns profit/drawdown tracker by account and id. See
        https://metaapi.cloud/docs/risk-management/restApi/api/getTracker/

        Args:
            account_id: ID of the MetaApi account.
            id: Tracker ID.

        Returns:
            A coroutine resolving with profit/drawdown tracker found.
        """
        return await self._domain_client.request_api(
            {
                'url': f'/users/current/accounts/{account_id}/trackers/{id}',
                'headers': {'auth-token': self._domain_client.token, 'api-version': '1'},
                'method': 'GET',
            }
        )

    async def get_tracker_by_name(self, account_id: str, name: str) -> Tracker:
        """Returns profit/drawdown tracker by account and name

        Args:
            account_id: ID of the MetaApi account.
            name: Tracker name.

        Returns:
            A coroutine resolving with profit/drawdown tracker found.
        """
        return await self._domain_client.request_api(
            {
                'url': f'/users/current/accounts/{account_id}/trackers/name/{parse.quote(name)}',
                'headers': {'auth-token': self._domain_client.token, 'api-version': '1'},
                'method': 'GET',
            }
        )

    async def update_tracker(self, account_id: str, id: str, update: TrackerUpdate):
        """Updates profit/drawdown tracker. See
        https://metaapi.cloud/docs/risk-management/restApi/api/updateTracker/

        Args:
            account_id: ID of the MetaApi account.
            id: ID of the tracker.
            update: Tracker update.

        Returns:
            A coroutine resolving when profit/drawdown tracker updated.
        """
        return await self._domain_client.request_api(
            {'url': f'/users/current/accounts/{account_id}/trackers/{id}', 'method': 'PUT', 'body': update}
        )

    async def delete_tracker(self, account_id: str, id: str):
        """Removes profit/drawdown tracker. See
        https://metaapi.cloud/docs/risk-management/restApi/api/removeTracker/

        Args:
            account_id: ID of the MetaApi account.
            id: ID of the tracker.

        Returns:
            A coroutine resolving when profit/drawdown tracker removed.
        """
        return await self._domain_client.request_api(
            {'url': f'/users/current/accounts/{account_id}/trackers/{id}', 'method': 'DELETE'}
        )

    async def get_tracker_events(
        self,
        start_broker_time: str = None,
        end_broker_time: str = None,
        account_id: str = None,
        tracker_id: str = None,
        limit: int = None,
    ) -> 'List[TrackerEvent]':
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
        qs = {}
        if start_broker_time is not None:
            qs['startBrokerTime'] = start_broker_time
        if end_broker_time is not None:
            qs['endBrokerTime'] = end_broker_time
        if account_id is not None:
            qs['accountId'] = account_id
        if tracker_id is not None:
            qs['trackerId'] = tracker_id
        if limit is not None:
            qs['limit'] = limit
        return await self._domain_client.request_api(
            {'url': '/users/current/tracker-events/by-broker-time', 'method': 'GET', 'params': qs}
        )

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
        return self._tracker_event_listener_manager.add_tracker_event_listener(
            listener, account_id, tracker_id, sequence_number
        )

    def remove_tracker_event_listener(self, listener_id: str):
        """Removes tracker event listener and cancels the event stream

        Args:
            listener_id: Tracker event listener id.
        """
        self._tracker_event_listener_manager.remove_tracker_event_listener(listener_id)

    async def get_tracking_statistics(
        self, account_id: str, tracker_id: str, start_time: str = None, limit: int = None, real_time: bool = False
    ) -> 'List[PeriodStatistics]':
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
        qs = {'realTime': real_time}
        if start_time is not None:
            qs['startTime'] = start_time
        if limit is not None:
            qs['limit'] = limit
        return await self._domain_client.request_api(
            {
                'url': f'/users/current/accounts/{account_id}/trackers/{tracker_id}/statistics',
                'headers': {'auth-token': self._domain_client.token, 'api-version': '1'},
                'method': 'GET',
                'params': qs,
            }
        )

    async def add_period_statistics_listener(
        self, listener: PeriodStatisticsListener, account_id: str, tracker_id: str
    ) -> str:
        """Adds a period statistics event listener.

        Args:
            listener: Period statistics event listener.
            account_id: Account ID.
            tracker_id: Tracker ID.

        Returns:
            Listener ID.
        """
        return await self._period_statistics_stream_manager.add_period_statistics_listener(
            listener, account_id, tracker_id
        )

    def remove_period_statistics_listener(self, listener_id: str):
        """Removes period statistics event listener by id

        Args:
            listener_id: Listener id.
        """
        self._period_statistics_stream_manager.remove_period_statistics_listener(listener_id)

    async def get_equity_chart(
        self,
        account_id: str,
        start_time: str = None,
        end_time: str = None,
        real_time: bool = False,
        fill_skips: bool = False,
    ) -> 'List[EquityChartItem]':
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
        qs = {'realTime': real_time}
        if start_time is not None:
            qs['startTime'] = start_time
        if end_time is not None:
            qs['endTime'] = end_time
        records = await self._domain_client.request_api(
            {
                'url': f'/users/current/accounts/{account_id}/equity-chart',
                'headers': {'auth-token': self._domain_client.token, 'api-version': '1'},
                'method': 'GET',
                'params': qs,
            }
        )
        if fill_skips:
            i = 0
            while i < len(records) - 1:
                time_diff = (
                    date(records[i + 1]['startBrokerTime']).timestamp()
                    - date(records[i]['startBrokerTime']).timestamp()
                )
                if time_diff > 60 * 60 and 'lastBalance' in records[i]:
                    record_copy = json.loads(json.dumps(records[i]))
                    record_copy['minEquity'] = record_copy['lastEquity']
                    record_copy['maxEquity'] = record_copy['lastEquity']
                    record_copy['averageEquity'] = record_copy['lastEquity']
                    record_copy['minBalance'] = record_copy['lastBalance']
                    record_copy['maxBalance'] = record_copy['lastBalance']
                    record_copy['averageBalance'] = record_copy['lastBalance']
                    start_broker_time = date(record_copy['startBrokerTime'])
                    start_broker_time.replace(minute=0, second=0, microsecond=0)
                    start_broker_time = start_broker_time + timedelta(hours=1)
                    record_copy['startBrokerTime'] = format_date_broker(start_broker_time)
                    start_broker_time = start_broker_time + timedelta(hours=1, milliseconds=-1)
                    record_copy['endBrokerTime'] = format_date_broker(start_broker_time)
                    record_copy['brokerTime'] = record_copy['endBrokerTime']
                    records.insert(i + 1, record_copy)
                i += 1
        return records

    async def add_equity_chart_listener(
        self, listener: EquityChartListener, account_id: str, start_time: datetime = None
    ) -> Coroutine[str, Any, None]:
        """Adds an equity chart event listener.

        Args:
            listener: Equity chart event listener.
            account_id: Account id.
            start_time: Date to start tracking from.

        Returns:
            Listener ID.
        """
        return await self._equity_chart_stream_manager.add_equity_chart_listener(listener, account_id, start_time)

    def remove_equity_chart_listener(self, listener_id: str):
        """Removes equity chart event listener by id.

        Args:
            listener_id: Equity chart listener id.
        """
        self._equity_chart_stream_manager.remove_equity_chart_listener(listener_id)

    async def add_equity_balance_listener(
        self, listener: EquityBalanceListener, account_id: str
    ) -> Coroutine[str, Any, None]:
        """Adds an equity balance event listener.

        Args:
            listener: Equity balance event listener.
            account_id: Account ID.

        Returns:
            Listener ID.
        """
        return await self._equity_balance_stream_manager.add_equity_balance_listener(listener, account_id)

    def remove_equity_balance_listener(self, listener_id: str):
        """Removes equity balance event listener by ID.

        Args:
            listener_id: Equity balance listener ID.
        """
        self._equity_balance_stream_manager.remove_equity_balance_listener(listener_id)
