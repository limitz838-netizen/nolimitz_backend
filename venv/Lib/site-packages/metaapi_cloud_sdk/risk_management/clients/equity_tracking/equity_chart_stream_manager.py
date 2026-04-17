import asyncio
import json
import math
from asyncio import Future
from datetime import datetime
from typing import Dict, List, Any, Coroutine

from .equity_chart_listener import EquityChartListener
from .equity_tracking_client_model import EquityTrackingClientModel
from ..domain_client import DomainClient
from ...models import random_id
from .... import MetaApi
from ....clients.metaapi.synchronization_listener import SynchronizationListener
from ....logger import LoggerManager
from ....metaapi.models import MetatraderSymbolPrice, MetatraderAccountInformation, date
from ....metaapi.streaming_metaapi_connection_instance import StreamingMetaApiConnectionInstance


class EquityChartStreamManager:
    """Manager for handling equity chart event listeners."""

    def __init__(
        self, domain_client: DomainClient, equity_tracking_client: EquityTrackingClientModel, metaapi: MetaApi
    ):
        """Constructs equity chart event listener manager instance.

        Args:
            domain_client: Domain client.
            equity_tracking_client: Equity tracking client.
            metaapi: MetaApi SDK instance.
        """
        self._domain_client = domain_client
        self._equity_tracking_client = equity_tracking_client
        self._metaapi = metaapi
        self._equity_chart_listeners: Dict[str, Dict[str, EquityChartListener]] = {}
        self._accounts_by_listener_id: Dict[str, str] = {}
        self._equity_chart_connections: Dict[str, StreamingMetaApiConnectionInstance] = {}
        self._equity_chart_caches = {}
        self._account_synchronization_flags: Dict[str, bool] = {}
        self._pending_initialization_resolves: Dict[str, List[Future]] = {}
        self._retry_interval_in_seconds = 1
        self._logger = LoggerManager.get_logger('EquityChartStreamManager')

    def get_account_listeners(self, account_id: str) -> Dict[str, EquityChartListener]:
        """Returns listeners for account.

        Args:
            account_id: Account id to return listeners for.

        Returns:
            Dictionary of account equity chart event listeners.
        """
        if account_id not in self._equity_chart_listeners:
            self._equity_chart_listeners[account_id] = {}
        return self._equity_chart_listeners[account_id]

    async def add_equity_chart_listener(
        self, listener: EquityChartListener, account_id: str, start_time: datetime = None
    ) -> Coroutine[str, Any, None]:
        """Adds an equity chart event listener.

        Args:
            listener: Equity chart event listener.
            account_id: Account id.
            start_time: Date to start tracking from.

        Returns:
            Listener id.
        """
        if account_id not in self._equity_chart_caches:
            self._equity_chart_caches[account_id] = {'record': {}, 'lastPeriod': {}, 'pendingInitalizationResolves': []}
        cache = self._equity_chart_caches[account_id]
        connection: StreamingMetaApiConnectionInstance = None
        retry_interval_in_seconds = self._retry_interval_in_seconds
        equity_tracking_client = self._equity_tracking_client

        def get_account_listeners():
            return self.get_account_listeners(account_id)

        pending_initialization_resolves = self._pending_initialization_resolves
        synchronization_flags = self._account_synchronization_flags

        class EquityChartStreamListener(SynchronizationListener):
            async def on_deals_synchronized(self, instance_index: str, synchronization_id: str):
                try:
                    if account_id not in synchronization_flags or not synchronization_flags[account_id]:
                        synchronization_flags[account_id] = True
                        for account_listener in get_account_listeners().values():
                            asyncio.create_task(account_listener.on_connected())
                    if account_id in pending_initialization_resolves:
                        for promise in pending_initialization_resolves[account_id]:
                            promise.set_result(True)
                            del pending_initialization_resolves[account_id]
                except Exception as err:
                    for account_listener in get_account_listeners().values():
                        asyncio.create_task(account_listener.on_error(err))
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
                        for account_listener in get_account_listeners().values():
                            asyncio.create_task(account_listener.on_disconnected())
                except Exception as err:
                    for account_listener in get_account_listeners().values():
                        asyncio.create_task(account_listener.on_error(err))
                    self._logger.error(
                        "Error processing on_disconnected event for"
                        f"equity chart listener for account {account_id}"
                        f"{err}"
                    )

            async def on_symbol_price_updated(self, instance_index: str, price: MetatraderSymbolPrice):
                try:
                    if account_id in pending_initialization_resolves:
                        for promise in pending_initialization_resolves[account_id]:
                            promise.set_result(True)
                            del pending_initialization_resolves[account_id]

                    equity = price['equity']
                    broker_time = price['brokerTime']
                    if 'lastPeriod' not in cache or 'endBrokerTime' not in cache['lastPeriod']:
                        return

                    if broker_time > cache['lastPeriod']['endBrokerTime']:
                        for account_listener in get_account_listeners().values():
                            asyncio.create_task(account_listener.on_equity_record_completed())

                        start_broker_time = cache['lastPeriod']['startBrokerTime']
                        cache['lastPeriod'] = None
                        while True:
                            periods = await equity_tracking_client.get_equity_chart(
                                account_id, start_broker_time, None, True
                            )
                            if len(periods) < 2:
                                await asyncio.sleep(10)
                            else:
                                for account_listener in get_account_listeners().values():
                                    asyncio.create_task(account_listener.on_equity_record_updated(periods))
                                cache['lastPeriod'] = periods[1]
                                break
                    else:
                        account_information = connection.terminal_state.account_information
                        if account_information:
                            previous_info = {
                                'startBrokerTime': cache['lastPeriod']['startBrokerTime'],
                                'endBrokerTime': cache['lastPeriod']['endBrokerTime'],
                                'averageBalance': cache['record']['averageBalance'],
                                'minBalance': cache['record']['minBalance'],
                                'maxBalance': cache['record']['maxBalance'],
                                'averageEquity': math.floor(cache['record']['averageEquity']),
                                'minEquity': cache['record']['minEquity'],
                                'maxEquity': cache['record']['maxEquity'],
                                'lastBalance': cache['lastPeriod']['lastBalance'],
                                'lastEquity': cache['lastPeriod']['lastEquity'],
                            }
                            duration_increment = (
                                date(broker_time).timestamp() - date(cache['lastPeriod']['brokerTime']).timestamp()
                            ) * 1000
                            cache['lastPeriod']['equitySum'] += duration_increment * (
                                cache['lastPeriod']['equity']
                                if cache['lastPeriod'] and 'equity' in cache['lastPeriod']
                                else account_information['equity']
                            )
                            cache['lastPeriod']['balanceSum'] += duration_increment * (
                                cache['lastPeriod']['balance']
                                if cache['lastPeriod'] and 'balance' in cache['lastPeriod']
                                else account_information['balance']
                            )
                            cache['lastPeriod']['duration'] += duration_increment
                            cache['lastPeriod']['equity'] = price['equity']
                            cache['lastPeriod']['balance'] = account_information['balance']
                            cache['lastPeriod']['brokerTime'] = price['brokerTime']
                            cache['record']['duration'] = cache['lastPeriod']['duration']
                            cache['record']['balanceSum'] = cache['lastPeriod']['balanceSum']
                            cache['record']['equitySum'] = cache['lastPeriod']['equitySum']
                            cache['record']['averageEquity'] = (
                                cache['lastPeriod']['equitySum'] / cache['lastPeriod']['duration']
                                if cache['lastPeriod'] and 'duration' in cache['lastPeriod']
                                else equity
                            )
                            cache['record']['averageBalance'] = (
                                cache['lastPeriod']['balanceSum'] / cache['lastPeriod']['duration']
                                if cache['lastPeriod'] and 'duration' in cache['lastPeriod']
                                else equity
                            )
                            cache['record']['minEquity'] = min(cache['record']['minEquity'], price['equity'])
                            cache['record']['maxEquity'] = max(cache['record']['maxEquity'], price['equity'])
                            cache['record']['lastEquity'] = equity
                            cache['record']['minBalance'] = min(
                                cache['record']['minBalance'], account_information['balance']
                            )
                            cache['record']['maxBalance'] = max(
                                cache['record']['maxBalance'], account_information['balance']
                            )
                            cache['record']['lastBalance'] = account_information['balance']
                            # due to calculation inaccuracy, averageEquity will never match the previous value
                            # therefore, floor before comparing
                            if cache['lastPeriod'] and 'startBrokerTime' in cache['lastPeriod']:
                                new_info = {
                                    'startBrokerTime': cache['lastPeriod']['startBrokerTime'],
                                    'endBrokerTime': cache['lastPeriod']['endBrokerTime'],
                                    'averageBalance': cache['record']['averageBalance'],
                                    'minBalance': cache['record']['minBalance'],
                                    'maxBalance': cache['record']['maxBalance'],
                                    'averageEquity': math.floor(cache['record']['averageEquity']),
                                    'minEquity': cache['record']['minEquity'],
                                    'maxEquity': cache['record']['maxEquity'],
                                    'lastBalance': cache['record']['lastBalance'],
                                    'lastEquity': cache['record']['lastEquity'],
                                }
                                if json.dumps(previous_info) != json.dumps(new_info):
                                    for account_listener in get_account_listeners().values():
                                        asyncio.create_task(account_listener.on_equity_record_updated([new_info]))
                except Exception as err:
                    for account_listener in get_account_listeners().values():
                        asyncio.create_task(account_listener.on_error(err))
                    self._logger.error(
                        "Error processing on_symbol_price_updated event for"
                        f"equity chart listener for account {account_id}"
                        f"{err}"
                    )

            async def on_account_information_updated(
                self, instance_index: str, account_information: MetatraderAccountInformation
            ):
                try:
                    balance = account_information['balance']
                    cache['lastPeriod']['balance'] = balance
                    cache['lastPeriod']['lastBalance'] = balance
                    cache['record']['lastBalance'] = balance
                    cache['record']['minBalance'] = (
                        min(cache['record']['minBalance'], balance) if 'minBalance' in cache['record'] else balance
                    )
                    cache['record']['maxBalance'] = (
                        max(cache['record']['maxBalance'], balance) if 'maxBalance' in cache['record'] else balance
                    )
                except Exception as err:
                    for account_listener in get_account_listeners().values():
                        asyncio.create_task(account_listener.on_error(err))
                    self._logger.error(
                        "Error processing on_account_information_updated event for"
                        f"equity chart listener for account {account_id}"
                        f"{err}"
                    )

        listener_id = random_id(10)
        account_listeners = self.get_account_listeners(account_id)
        account_listeners[listener_id] = listener
        self._accounts_by_listener_id[listener_id] = account_id
        account = await self._metaapi.metatrader_account_api.get_account(account_id)
        is_deployed = False
        while not is_deployed:
            try:
                await account.wait_deployed()
                is_deployed = True
            except Exception as err:
                asyncio.create_task(listener.on_error(err))
                self._logger.error(f'Error wait for account {account_id} to deploy, retrying', err)
                await asyncio.sleep(retry_interval_in_seconds)
                retry_interval_in_seconds = min(retry_interval_in_seconds * 2, 300)

        if account_id not in self._equity_chart_connections:
            retry_interval_in_seconds = self._retry_interval_in_seconds
            connection = account.get_streaming_connection()
            self._equity_chart_connections[account_id] = connection
            sync_listener = EquityChartStreamListener()
            connection.add_synchronization_listener(sync_listener)

            is_synchronized = False
            while not is_synchronized:
                try:
                    await connection.connect()
                    await connection.wait_synchronized()
                    is_synchronized = True
                except Exception as err:
                    asyncio.create_task(listener.on_error(err))
                    self._logger.error(
                        'Error configuring equity chart stream listener ' + f'for account {account_id}, retrying', err
                    )
                    await asyncio.sleep(retry_interval_in_seconds)
                    retry_interval_in_seconds = min(retry_interval_in_seconds * 2, 300)

            retry_interval_in_seconds = self._retry_interval_in_seconds
        else:
            connection = self._equity_chart_connections[account_id]
            if not connection.health_monitor.health_status['synchronized']:
                if account_id not in self._pending_initialization_resolves:
                    self._pending_initialization_resolves[account_id] = []
                initialize_promise = asyncio.Future()
                self._pending_initialization_resolves[account_id].append(initialize_promise)
                await initialize_promise

        initial_data = []
        while not len(initial_data):
            try:
                initial_data = await equity_tracking_client.get_equity_chart(account_id, start_time, None, True)
                if len(initial_data):
                    last_item = initial_data[-1]
                    asyncio.create_task(listener.on_equity_record_updated(initial_data))
                    cache['lastPeriod'] = {
                        'duration': last_item['duration'],
                        'equitySum': last_item['equitySum'],
                        'balanceSum': last_item['balanceSum'],
                        'startBrokerTime': last_item['startBrokerTime'],
                        'endBrokerTime': last_item['endBrokerTime'],
                        'brokerTime': last_item['brokerTime'],
                        'averageEquity': math.floor(last_item['averageEquity']),
                        'minEquity': last_item['minEquity'],
                        'maxEquity': last_item['maxEquity'],
                        'averageBalance': last_item['averageBalance'],
                        'minBalance': last_item['minBalance'],
                        'maxBalance': last_item['maxBalance'],
                        'lastBalance': last_item.get('lastBalance'),
                        'lastEquity': last_item.get('lastEquity'),
                    }
                    cache['record'] = cache['lastPeriod']
            except Exception as err:
                asyncio.create_task(listener.on_error(err))
                self._logger.error(f'Failed initialize equity chart data for account {account_id}', err)
                await asyncio.sleep(retry_interval_in_seconds)
                retry_interval_in_seconds = min(retry_interval_in_seconds * 2, 300)
        return listener_id

    def remove_equity_chart_listener(self, listener_id: str):
        """Removes equity chart event listener by id.

        Args:
            listener_id: Listener id.
        """
        if listener_id in self._accounts_by_listener_id:
            account_id = self._accounts_by_listener_id[listener_id]
            if account_id in self._account_synchronization_flags:
                del self._account_synchronization_flags[account_id]
            if listener_id in self._accounts_by_listener_id:
                del self._accounts_by_listener_id[listener_id]
            if account_id in self._equity_chart_listeners and listener_id in self._equity_chart_listeners[account_id]:
                del self._equity_chart_listeners[account_id][listener_id]
            if account_id in self._equity_chart_connections and not len(
                self._equity_chart_listeners[account_id].keys()
            ):
                asyncio.create_task(self._equity_chart_connections[account_id].close())
                del self._equity_chart_connections[account_id]
