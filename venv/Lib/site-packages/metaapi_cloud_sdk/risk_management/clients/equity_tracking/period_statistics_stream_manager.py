import asyncio
import json
from asyncio import Future
from datetime import datetime
from functools import reduce
from typing import List, Dict

from .equity_tracking_client_model import EquityTrackingClientModel
from .period_statistics_listener import PeriodStatisticsListener
from ..domain_client import DomainClient
from ...models import random_id, date
from .... import MetaApi
from ....clients.metaapi.synchronization_listener import SynchronizationListener
from ....logger import LoggerManager
from ....metaapi.streaming_metaapi_connection_instance import StreamingMetaApiConnectionInstance


class PeriodStatisticsStreamManager:
    """Manager for handling period statistics event listeners."""

    def __init__(
        self, domain_client: DomainClient, equity_tracking_client: EquityTrackingClientModel, metaapi: MetaApi
    ):
        """Constructs period statistics event listener manager instance.

        Args:
            domain_client: Domain client.
            equity_tracking_client: Equity tracking client.
            metaapi: MetaApi SDK instance.
        """
        self._domain_client = domain_client
        self._equity_tracking_client = equity_tracking_client
        self._metaapi = metaapi
        self._period_statistics_listeners = {}
        self._accounts_by_listener_id: Dict[str, str] = {}
        self._tracker_by_listener_id = {}
        self._tracker_sync_listeners = {}
        self._period_statistics_connections: Dict[str, StreamingMetaApiConnectionInstance] = {}
        self._period_statistics_caches = {}
        self._account_synchronization_flags: Dict[str, bool] = {}
        self._pending_initialization_resolves: Dict[str, List[Future]] = {}
        self._sync_listeners = {}
        self._retry_interval_in_seconds = 1
        self._fetch_initial_data_interval_id = {}
        self._logger = LoggerManager.get_logger('PeriodStatisticsStreamManager')

    def get_tracker_listeners(self, account_id: str, tracker_id: str) -> Dict[str, PeriodStatisticsListener]:
        """Returns listeners for account.

        Args:
            account_id: Account id to return listeners for.

        Returns:
            Dictionary of account equity chart event listeners.
        """
        if not self._period_statistics_listeners.get(account_id) or not self._period_statistics_listeners[
            account_id
        ].get(tracker_id):
            return {}
        else:
            return self._period_statistics_listeners[account_id][tracker_id]

    async def add_period_statistics_listener(
        self, listener: PeriodStatisticsListener, account_id: str, tracker_id: str
    ):
        """Adds a period statistics event listener.

        Args:
            listener: Period statistics event listener.
            account_id: Account id.
            tracker_id: Tracker id.

        Returns:
            Listener id.
        """
        new_tracker = False
        if not self._period_statistics_caches.get(account_id):
            self._period_statistics_caches[account_id] = {}
        if not self._period_statistics_caches[account_id].get(tracker_id):
            new_tracker = True
            self._period_statistics_caches[account_id][tracker_id] = {
                'trackerData': {},
                'record': {},
                'lastPeriod': {},
                'equityAdjustments': {},
            }
        cache = self._period_statistics_caches[account_id][tracker_id]
        connection: StreamingMetaApiConnectionInstance = None
        retry_interval_in_seconds = self._retry_interval_in_seconds
        equity_tracking_client = self._equity_tracking_client
        listener_id = random_id(10)

        def remove_period_statistics_listener(listener_id):
            return self.remove_period_statistics_listener(listener_id)

        def get_tracker_listeners():
            return self.get_tracker_listeners(account_id, tracker_id)

        pending_initialization_resolves = self._pending_initialization_resolves
        synchronization_flags = self._account_synchronization_flags

        class PeriodStatisticsStreamListener(SynchronizationListener):
            async def on_deals_synchronized(self, instance_index: str, synchronization_id: str):
                try:
                    if account_id not in synchronization_flags or not synchronization_flags[account_id]:
                        synchronization_flags[account_id] = True
                        for account_listener in get_tracker_listeners().values():
                            asyncio.create_task(account_listener.on_connected())
                    if account_id in pending_initialization_resolves:
                        for promise in pending_initialization_resolves[account_id]:
                            promise.set_result(True)
                            del pending_initialization_resolves[account_id]
                except Exception as err:
                    asyncio.create_task(listener.on_error(err))
                    self._logger.error(
                        "Error processing on_deals_synchronized event for"
                        f"equity chart listener for account {account_id}"
                        f"{err}"
                    )

            async def on_disconnected(self, instance_index: str):
                try:
                    if (
                        account_id in synchronization_flags
                        and not connection.health_monitor.health_status['synchronized']
                    ):
                        synchronization_flags[account_id] = False
                        for tracker_listener in get_tracker_listeners().values():
                            asyncio.create_task(tracker_listener.on_disconnected())
                except Exception as err:
                    for tracker_listener in get_tracker_listeners().values():
                        asyncio.create_task(tracker_listener.on_error(err))
                    self._logger.error(
                        "Error processing on_disconnected event for"
                        f"equity chart listener for account {account_id}"
                        f"{err}"
                    )

            async def on_symbol_price_updated(self, instance_index: str, price):
                try:
                    if account_id in pending_initialization_resolves:
                        for promise in pending_initialization_resolves[account_id]:
                            promise.set_result(True)
                            del pending_initialization_resolves[account_id]

                    if not cache['lastPeriod']:
                        return

                    """Process brokerTime:
                    - smaller than tracker startBrokerTime -> ignore
                    - bigger than tracker endBrokerTime -> send on_tracker_completed, close connection
                    - bigger than period endBrokerTime -> send on_period_statistics_completed
                    - normal -> compare to previous data, if different -> send on_period_statistics_updated
                    """

                    equity = price['equity'] - reduce(lambda a, b: a + b, cache['equityAdjustments'].values(), 0)
                    broker_time = price['brokerTime']
                    if broker_time > cache['lastPeriod']['endBrokerTime']:
                        for tracker_listener in get_tracker_listeners().values():
                            asyncio.create_task(tracker_listener.on_period_statistics_completed())
                        cache['equityAdjustments'] = {}
                        start_broker_time = cache['lastPeriod']['startBrokerTime']
                        cache['lastPeriod'] = None
                        while True:
                            periods = await equity_tracking_client.get_tracking_statistics(
                                account_id, tracker_id, None, 2, True
                            )
                            if periods[0]['startBrokerTime'] == start_broker_time:
                                await asyncio.sleep(10)
                            else:
                                cache['lastPeriod'] = periods[0]
                                periods.reverse()
                                for tracker_listener in get_tracker_listeners().values():
                                    asyncio.create_task(tracker_listener.on_period_statistics_updated(periods))
                                break
                    else:
                        if (
                            'startBrokerTime' in cache['trackerData']
                            and broker_time < cache['trackerData']['startBrokerTime']
                        ):
                            return

                        if (
                            'endBrokerTime' in cache['trackerData']
                            and broker_time > cache['trackerData']['endBrokerTime']
                        ):
                            for tracker_listener in get_tracker_listeners().values():
                                asyncio.create_task(tracker_listener.on_tracker_completed())
                            cache['equityAdjustments'] = {}
                            for tracker_listener_id in get_tracker_listeners().copy():
                                remove_period_statistics_listener(tracker_listener_id)

                        absolute_drawdown = max(0, cache['lastPeriod']['initialBalance'] - equity)
                        relative_drawdown = absolute_drawdown / cache['lastPeriod']['initialBalance']
                        absolute_profit = max(0, equity - cache['lastPeriod']['initialBalance'])
                        relative_profit = absolute_profit / cache['lastPeriod']['initialBalance']
                        previous_record = json.dumps(cache['record'])
                        if not cache['record']['thresholdExceeded']:
                            if cache['record']['maxAbsoluteDrawdown'] < absolute_drawdown:
                                cache['record']['maxAbsoluteDrawdown'] = absolute_drawdown
                                cache['record']['maxRelativeDrawdown'] = relative_drawdown
                                cache['record']['maxDrawdownTime'] = broker_time
                                if (
                                    'relativeDrawdownThreshold' in cache['trackerData']
                                    and cache['trackerData']['relativeDrawdownThreshold']
                                    and cache['trackerData']['relativeDrawdownThreshold'] < relative_drawdown
                                    or 'absoluteDrawdownThreshold' in cache['trackerData']
                                    and cache['trackerData']['absoluteDrawdownThreshold']
                                    and cache['trackerData']['absoluteDrawdownThreshold'] < absolute_drawdown
                                ):
                                    cache['record']['thresholdExceeded'] = True
                                    cache['record']['exceededThresholdType'] = 'drawdown'

                            if cache['record']['maxAbsoluteProfit'] < absolute_profit:
                                cache['record']['maxAbsoluteProfit'] = absolute_profit
                                cache['record']['maxRelativeProfit'] = relative_profit
                                cache['record']['maxProfitTime'] = broker_time
                                if (
                                    'relativeProfitThreshold' in cache['trackerData']
                                    and cache['trackerData']['relativeProfitThreshold']
                                    and cache['trackerData']['relativeProfitThreshold'] < relative_profit
                                    or 'absoluteProfitThreshold' in cache['trackerData']
                                    and cache['trackerData']['absoluteProfitThreshold']
                                    and cache['trackerData']['absoluteProfitThreshold'] < absolute_profit
                                ):
                                    cache['record']['thresholdExceeded'] = True
                                    cache['record']['exceededThresholdType'] = 'profit'
                            if json.dumps(cache['record']) != previous_record:
                                for tracker_listener in get_tracker_listeners().values():
                                    asyncio.create_task(
                                        tracker_listener.on_period_statistics_updated(
                                            [
                                                {
                                                    'startBrokerTime': cache['lastPeriod']['startBrokerTime'],
                                                    'endBrokerTime': cache['lastPeriod']['endBrokerTime'],
                                                    'initialBalance': cache['lastPeriod']['initialBalance'],
                                                    'maxAbsoluteDrawdown': cache['record']['maxAbsoluteDrawdown'],
                                                    'maxAbsoluteProfit': cache['record']['maxAbsoluteProfit'],
                                                    'maxDrawdownTime': cache['record']['maxDrawdownTime'],
                                                    'maxProfitTime': cache['record']['maxProfitTime'],
                                                    'maxRelativeDrawdown': cache['record']['maxRelativeDrawdown'],
                                                    'maxRelativeProfit': cache['record']['maxRelativeProfit'],
                                                    'period': cache['lastPeriod']['period'],
                                                    'exceededThresholdType': cache['record']['exceededThresholdType'],
                                                    'thresholdExceeded': cache['record']['thresholdExceeded'],
                                                    'tradeDayCount': cache['record']['tradeDayCount'],
                                                }
                                            ]
                                        )
                                    )
                except Exception as err:
                    for tracker_listener in get_tracker_listeners().values():
                        asyncio.create_task(tracker_listener.on_error(err))
                    self._logger.error(
                        "Error processing on_symbol_price_updated event for"
                        f"equity chart listener for account {account_id}"
                        f"{err}"
                    )

            async def on_deal_added(self, instance_index: str, deal):
                try:
                    if not cache['lastPeriod'] or not len(cache['lastPeriod']):
                        return

                    if deal['type'] == 'DEAL_TYPE_BALANCE':
                        cache['equityAdjustments'][deal['id']] = deal['profit']
                    ignored_deal_types = ['DEAL_TYPE_BALANCE', 'DEAL_TYPE_CREDIT']
                    if deal['type'] not in ignored_deal_types:
                        time_diff = date(deal['time']).timestamp() - date(deal['brokerTime']).timestamp()
                        start_search_date = datetime.fromtimestamp(
                            date(cache['lastPeriod']['startBrokerTime']).timestamp() + time_diff
                        )
                        deals = list(
                            filter(
                                lambda deal_item: deal_item['type'] not in ignored_deal_types,
                                connection.history_storage.get_deals_by_time_range(start_search_date, date(8640000000)),
                            )
                        )
                        deals.append(deal)
                        traded_days = {}
                        for deal_item in deals:
                            traded_days[deal_item['brokerTime'][0:10]] = True
                        trade_day_count = len(traded_days.keys())
                        if cache['record']['tradeDayCount'] != trade_day_count:
                            cache['record']['tradeDayCount'] = trade_day_count
                            for tracker_listener in get_tracker_listeners().values():
                                asyncio.create_task(
                                    tracker_listener.on_period_statistics_updated(
                                        [
                                            {
                                                'startBrokerTime': cache['lastPeriod']['startBrokerTime'],
                                                'endBrokerTime': cache['lastPeriod']['endBrokerTime'],
                                                'initialBalance': cache['lastPeriod']['initialBalance'],
                                                'maxAbsoluteDrawdown': cache['record']['maxAbsoluteDrawdown'],
                                                'maxAbsoluteProfit': cache['record']['maxAbsoluteProfit'],
                                                'maxDrawdownTime': cache['record']['maxDrawdownTime'],
                                                'maxProfitTime': cache['record']['maxProfitTime'],
                                                'maxRelativeDrawdown': cache['record']['maxRelativeDrawdown'],
                                                'maxRelativeProfit': cache['record']['maxRelativeProfit'],
                                                'period': cache['lastPeriod']['period'],
                                                'exceededThresholdType': cache['record']['exceededThresholdType'],
                                                'thresholdExceeded': cache['record']['thresholdExceeded'],
                                                'tradeDayCount': cache['record']['tradeDayCount'],
                                            }
                                        ]
                                    )
                                )
                except Exception as err:
                    for tracker_listener in get_tracker_listeners().values():
                        asyncio.create_task(tracker_listener.on_error(err))
                    self._logger.error(
                        "Error processing on_deal_added event for"
                        f"equity chart listener for account {account_id}"
                        f"{err}"
                    )

        account = await self._metaapi.metatrader_account_api.get_account(account_id)
        tracker = await equity_tracking_client.get_tracker(account_id, tracker_id)
        cache['trackerData'] = tracker
        if account_id not in self._period_statistics_listeners:
            self._period_statistics_listeners[account_id] = {}
        if tracker_id not in self._period_statistics_listeners[account_id]:
            self._period_statistics_listeners[account_id][tracker_id] = {}
        account_listeners = self._period_statistics_listeners[account_id][tracker_id]
        account_listeners[listener_id] = listener
        self._accounts_by_listener_id[listener_id] = account_id
        self._tracker_by_listener_id[listener_id] = tracker_id
        is_deployed = False
        while not is_deployed:
            try:
                await account.wait_deployed()
                is_deployed = True
            except Exception as err:
                asyncio.create_task(listener.on_error(err))
                self._logger.error(
                    f'Error wait for account {account_id} to deploy, retrying', MetaApi.format_error(err)
                )
                await asyncio.sleep(retry_interval_in_seconds)
                retry_interval_in_seconds = min(retry_interval_in_seconds * 2, 300)

        if account_id not in self._period_statistics_connections:
            retry_interval_in_seconds = self._retry_interval_in_seconds
            connection = account.get_streaming_connection()
            self._period_statistics_connections[account_id] = connection
            sync_listener = PeriodStatisticsStreamListener()
            connection.add_synchronization_listener(sync_listener)
            self._period_statistics_connections[account_id] = connection
            self._sync_listeners[tracker_id] = sync_listener

            is_synchronized = False
            while not is_synchronized:
                try:
                    await connection.connect()
                    await connection.wait_synchronized()
                    is_synchronized = True
                except Exception as err:
                    asyncio.create_task(listener.on_error(err))
                    self._logger.error(
                        'Error configuring period statistics stream listener ' + f'for account {account_id}, retrying',
                        MetaApi.format_error(err),
                    )
                    await asyncio.sleep(retry_interval_in_seconds)
                    retry_interval_in_seconds = min(retry_interval_in_seconds * 2, 300)

            retry_interval_in_seconds = self._retry_interval_in_seconds
        else:
            connection = self._period_statistics_connections[account_id]
            if new_tracker:
                sync_listener = PeriodStatisticsStreamListener()
                connection.add_synchronization_listener(sync_listener)
                self._sync_listeners[tracker_id] = sync_listener
            if not connection.health_monitor.health_status['synchronized']:
                if account_id not in self._pending_initialization_resolves:
                    self._pending_initialization_resolves[account_id] = []
                initialize_promise = asyncio.Future()
                self._pending_initialization_resolves[account_id].append(initialize_promise)
                await initialize_promise

        initial_data = []

        async def fetch_initial_data():
            try:
                nonlocal initial_data
                initial_data = await equity_tracking_client.get_tracking_statistics(
                    account_id, tracker_id, None, None, True
                )
                if len(initial_data):
                    last_item = initial_data[0]
                    if self._fetch_initial_data_interval_id.get(listener_id):
                        self._fetch_initial_data_interval_id[listener_id].cancel()
                        del self._fetch_initial_data_interval_id[listener_id]
                    asyncio.create_task(listener.on_period_statistics_updated(initial_data))
                    cache['lastPeriod'] = {
                        'startBrokerTime': last_item['startBrokerTime'],
                        'endBrokerTime': last_item.get('endBrokerTime'),
                        'period': last_item['period'],
                        'initialBalance': last_item['initialBalance'],
                        'maxDrawdownTime': last_item.get('maxDrawdownTime'),
                        'maxAbsoluteDrawdown': last_item.get('maxAbsoluteDrawdown'),
                        'maxRelativeDrawdown': last_item.get('maxRelativeDrawdown'),
                        'maxProfitTime': last_item.get('maxProfitTime'),
                        'maxAbsoluteProfit': last_item.get('maxAbsoluteProfit'),
                        'maxRelativeProfit': last_item.get('maxRelativeProfit'),
                        'thresholdExceeded': last_item['thresholdExceeded'],
                        'exceededThresholdType': last_item.get('exceededThresholdType'),
                        'tradeDayCount': last_item.get('tradeDayCount'),
                    }
                    cache['record'] = cache['lastPeriod']
            except Exception as err:
                asyncio.create_task(listener.on_error(err))
                self._logger.error(
                    f'Failed initialize tracking statistics data for account {account_id}', MetaApi.format_error(err)
                )
                nonlocal retry_interval_in_seconds
                await asyncio.sleep(retry_interval_in_seconds * 1)
                retry_interval_in_seconds = min(retry_interval_in_seconds * 2, 300)

        async def fetch_initial_data_task():
            while True:
                await fetch_initial_data()
                await asyncio.sleep(self._retry_interval_in_seconds * 2 * 60)

        self._fetch_initial_data_interval_id[listener_id] = asyncio.create_task(fetch_initial_data_task())

        return listener_id

    def remove_period_statistics_listener(self, listener_id: str):
        """Removes period statistics event listener by id.

        Args:
            listener_id: Listener id.
        """
        if listener_id in self._accounts_by_listener_id and listener_id in self._tracker_by_listener_id:
            if self._fetch_initial_data_interval_id.get(listener_id):
                self._fetch_initial_data_interval_id[listener_id].cancel()
                del self._fetch_initial_data_interval_id[listener_id]
            account_id = self._accounts_by_listener_id[listener_id]
            tracker_id = self._tracker_by_listener_id[listener_id]
            del self._accounts_by_listener_id[listener_id]
            del self._tracker_by_listener_id[listener_id]
            if account_id in self._period_statistics_listeners:
                if tracker_id in self._period_statistics_listeners[account_id]:
                    if listener_id in self._period_statistics_listeners[account_id][tracker_id]:
                        del self._period_statistics_listeners[account_id][tracker_id][listener_id]
                        if not len(self._period_statistics_listeners[account_id][tracker_id]):
                            del self._period_statistics_listeners[account_id][tracker_id]
                            # probably mistake in next line
                            if self._period_statistics_connections[account_id] and tracker_id in self._sync_listeners:
                                self._period_statistics_connections[account_id].remove_synchronization_listener(
                                    self._sync_listeners[tracker_id]
                                )
                                del self._sync_listeners[tracker_id]

                if not len(self._period_statistics_listeners[account_id]):
                    del self._period_statistics_listeners[account_id]

            if account_id in self._period_statistics_connections and not self._period_statistics_listeners.get(
                account_id
            ):
                if account_id in self._account_synchronization_flags:
                    del self._account_synchronization_flags[account_id]
                asyncio.create_task(self._period_statistics_connections[account_id].close())
                del self._period_statistics_connections[account_id]
