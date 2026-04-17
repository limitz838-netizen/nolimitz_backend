import asyncio
from datetime import datetime, timedelta
from functools import reduce
from random import uniform
from typing import Coroutine, List, Optional, Union

import pytz
from typing_extensions import TypedDict

from .connection_health_monitor import ConnectionHealthMonitor
from .connection_registry_model import ConnectionRegistryModel
from .history_storage import HistoryStorage
from .memory_history_storage import MemoryHistoryStorage
from .metaapi_connection import MetaApiConnection
from .metatrader_account_model import MetatraderAccountModel
from .models import (
    random_id,
    string_format_error,
    MarketDataSubscription,
    MarketDataUnsubscription,
    MetatraderSymbolSpecification,
    format_error,
)
from .terminal_hash_manager import TerminalHashManager
from .terminal_state import TerminalState
from ..clients.error_handler import ValidationException
from ..clients.metaapi.metaapi_websocket_client import MetaApiWebsocketClient
from ..clients.options_validator import OptionsValidator
from ..clients.timeout_exception import TimeoutException
from ..logger import LoggerManager


class MetaApiConnectionDict(TypedDict, total=False):
    instanceIndex: int
    ordersSynchronized: dict
    dealsSynchronized: dict
    shouldSynchronize: Optional[str]
    synchronizationRetryIntervalInSeconds: float
    synchronized: bool
    lastDisconnectedSynchronizationId: Optional[str]
    lastSynchronizationId: Optional[str]
    disconnected: bool
    synchronizationTimeout: Union[asyncio.Task, None]
    ensureSynchronizeTimeout: Union[asyncio.Task, None]


class SynchronizationOptions(TypedDict, total=False):
    instanceIndex: Optional[int]
    """Index of an account instance to ensure synchronization on, default is to wait for the first instance to
    synchronize."""
    applicationPattern: Optional[str]
    """Application regular expression pattern, default is .*"""
    synchronizationId: Optional[str]
    """synchronization id, last synchronization request id will be used by default"""
    timeoutInSeconds: Optional[float]
    """Wait timeout in seconds, default is 5m."""
    intervalInMilliseconds: Optional[float]
    """Interval between account reloads while waiting for a change, default is 1s."""


class StreamingMetaApiConnection(MetaApiConnection):
    """Exposes MetaApi MetaTrader streaming API connection to consumers."""

    def __init__(
        self,
        options,
        websocket_client: MetaApiWebsocketClient,
        terminal_hash_manager: TerminalHashManager,
        account: MetatraderAccountModel,
        history_storage: Union[HistoryStorage, None],
        connection_registry: ConnectionRegistryModel,
        history_start_time: datetime = None,
        refresh_subscriptions_opts: dict = None,
    ):
        """Initializes MetaApi MetaTrader streaming Api connection.

        Args:
            options: MetaApi options.
            websocket_client: MetaApi websocket client.
            terminal_hash_manager: Client api client.
            account: MetaTrader account id to connect to.
            history_storage: Local terminal history storage. By default, an instance of MemoryHistoryStorage
            will be used.
            history_start_time: History start sync time.
            refresh_subscriptions_opts: Subscriptions refresh options.
        """
        super().__init__(options, websocket_client, account)
        if refresh_subscriptions_opts is None:
            refresh_subscriptions_opts = {}
        validator = OptionsValidator()
        self._min_subscription_refresh_interval = validator.validate_non_zero(
            refresh_subscriptions_opts['minDelayInSeconds']
            if 'minDelayInSeconds' in refresh_subscriptions_opts
            else None,
            1,
            'refreshSubscriptionsOpts.minDelayInSeconds',
        )
        self._max_subscription_refresh_interval = validator.validate_non_zero(
            refresh_subscriptions_opts['maxDelayInSeconds']
            if 'maxDelayInSeconds' in refresh_subscriptions_opts
            else None,
            600,
            'refreshSubscriptionsOpts.maxDelayInSeconds',
        )
        self._closed = False
        self._opened = False
        self._latency_service = websocket_client.latency_service
        self._connection_registry = connection_registry
        self._history_start_time = history_start_time
        self._terminal_hash_manager = terminal_hash_manager
        self._terminal_state = TerminalState(account, terminal_hash_manager, self._websocket_client)
        self._history_storage = history_storage or MemoryHistoryStorage()
        self._health_monitor = ConnectionHealthMonitor(self)
        self._websocket_client.add_synchronization_listener(account.id, self)
        self._websocket_client.add_synchronization_listener(account.id, self._terminal_state)
        self._websocket_client.add_synchronization_listener(account.id, self._history_storage)
        self._websocket_client.add_synchronization_listener(account.id, self._health_monitor)
        self._websocket_client.add_reconnect_listener(self, account.id)
        for replica_id in account.account_regions.values():
            self._websocket_client.add_reconnect_listener(self, replica_id)
        self._subscriptions = {}
        self._state_by_instance_index = {}
        self._refresh_market_data_subscription_sessions = {}
        self._refresh_market_data_subscription_timeouts = {}
        self._opened_instances = []
        self._logger = LoggerManager.get_logger('MetaApiConnection')

    async def connect(self, instance_id: str):
        """Opens the connection. Can only be called the first time, next calls will be ignored.

        Args:
            instance_id: Connection instance id.

        Returns:
            A coroutine resolving when the connection is opened
        """
        if instance_id not in self._opened_instances:
            self._opened_instances.append(instance_id)
        if not self._opened:
            self._logger.debug(f'{self._account.id}: Opening connection')
            self._opened = True
            try:
                await self.initialize()
                await self.subscribe()
            except Exception as err:
                await self.close(instance_id)
                raise err

    def remove_application(self):
        """Clears the order and transaction history of a specified application and removes application.

        Returns:
            A coroutine resolving when the history is cleared and application is removed.
        """
        self._check_is_connection_active()
        asyncio.create_task(self._history_storage.clear())
        return self._websocket_client.remove_application(self._account.id)

    async def synchronize(self, instance_index: str) -> Coroutine:
        """Requests the terminal to start synchronization process.
        (see https://metaapi.cloud/docs/client/websocket/synchronizing/synchronize/).

        Args:
            instance_index: Instance index.

        Returns:
            A coroutine which resolves when synchronization started.
        """
        self._check_is_connection_active()
        region = self.get_region(instance_index)
        instance = self.get_instance_number(instance_index)
        host = self.get_host_name(instance_index)
        starting_history_order_time = datetime.utcfromtimestamp(
            max(
                ((self._history_start_time and self._history_start_time.timestamp()) or 0),
                (await self._history_storage.last_history_order_time(instance)).timestamp(),
            )
        ).replace(tzinfo=pytz.UTC)
        starting_deal_time = datetime.utcfromtimestamp(
            max(
                ((self._history_start_time and self._history_start_time.timestamp()) or 0),
                (await self._history_storage.last_deal_time(instance)).timestamp(),
            )
        ).replace(tzinfo=pytz.UTC)
        synchronization_id = random_id()
        self._get_state(instance_index)['lastSynchronizationId'] = synchronization_id
        account_id = self._account.account_regions[region]
        self._logger.debug(f'{self._account.id}:{instance_index}: initiating synchronization {synchronization_id}')

        return await self._websocket_client.synchronize(
            account_id,
            instance,
            host,
            synchronization_id,
            starting_history_order_time,
            starting_deal_time,
            self._terminal_state.get_hashes(),
        )

    async def initialize(self):
        """Initializes meta api connection"""
        self._check_is_connection_active()
        await self._history_storage.initialize(self._account.id, self._connection_registry.application)
        self._websocket_client.add_account_cache(self._account.id, self._account.account_regions)

    async def subscribe(self):
        """Initiates subscription to MetaTrader terminal.

        Returns:
            A coroutine which resolves when subscription is initiated.
        """
        self._check_is_connection_active()
        account_regions = self._account.account_regions
        for region, replica_id in account_regions.items():
            if not self._options.get('region') or self._options['region'] == region:
                self._websocket_client.ensure_subscribe(replica_id, 0)
                self._websocket_client.ensure_subscribe(replica_id, 1)

    async def subscribe_to_market_data(
        self,
        symbol: str,
        subscriptions: List[MarketDataSubscription] = None,
        timeout_in_seconds: float = None,
        wait_for_quote: bool = True,
    ) -> Coroutine:
        """Subscribes on market data of specified symbol (see
        https://metaapi.cloud/docs/client/websocket/marketDataStreaming/subscribeToMarketData/).

        Args:
            symbol: Symbol (e.g. currency pair or an index).
            subscriptions: Array of market data subscription to create or update. Please note that this feature is
            not fully implemented on server-side yet.
            timeout_in_seconds: Timeout to wait for prices in seconds, default is 30.
            wait_for_quote: if set to false, the method will resolve without waiting for the first quote to arrive.
            Default is to wait for quote if quotes subscription is requested

        Returns:
            Promise which resolves when subscription request was processed.
        """
        self._check_is_connection_active()
        if self._terminal_state.specification(symbol) is None:
            raise ValidationException(
                f'{self._account.id} Cannot subscribe to market data for symbol {symbol} because symbol '
                f'does not exist'
            )
        else:
            subscriptions = subscriptions or [{'type': 'quotes'}]
            if symbol in self._subscriptions:
                prev_subscriptions = self._subscriptions[symbol]['subscriptions'] or []
                for subscription in subscriptions:
                    index = -1
                    for i in range(len(prev_subscriptions)):
                        item = prev_subscriptions[i]
                        if subscription['type'] == 'candles':
                            if item['type'] == subscription['type'] and item['timeframe'] == subscription['timeframe']:
                                index = i
                                break
                        elif item['type'] == subscription['type']:
                            index = i
                            break
                    if index == -1:
                        prev_subscriptions.append(subscription)
                    else:
                        prev_subscriptions[index] = subscription
            else:
                self._subscriptions[symbol] = {'subscriptions': subscriptions}
            await self._websocket_client.subscribe_to_market_data(
                self._account.id, symbol, subscriptions, self._account.reliability
            )
            if wait_for_quote and next((s for s in subscriptions if s['type'] == 'quotes'), None):
                return await self.terminal_state.wait_for_price(symbol, timeout_in_seconds)

    def unsubscribe_from_market_data(
        self, symbol: str, unsubscriptions: List[MarketDataUnsubscription] = None
    ) -> Coroutine:
        """Unsubscribes from market data of specified symbol (see
        https://metaapi.cloud/docs/client/websocket/marketDataStreaming/subscribeToMarketData/).

        Args:
            symbol: Symbol (e.g. currency pair or an index).
            unsubscriptions: Array of subscriptions to cancel.

        Returns:
            Promise which resolves when subscription request was processed.
        """
        self._check_is_connection_active()
        if not unsubscriptions:
            if symbol in self._subscriptions:
                del self._subscriptions[symbol]
        elif symbol in self._subscriptions:
            self._subscriptions[symbol]['subscriptions'] = list(
                filter(
                    lambda subscription: not next(
                        (
                            unsubscription
                            for unsubscription in unsubscriptions
                            if (
                                subscription['type'] == unsubscription['type']
                                and (
                                    not unsubscription.get('timeframe')
                                    or subscription['timeframe'] == unsubscription['timeframe']
                                )
                            )
                        ),
                        None,
                    ),
                    self._subscriptions[symbol]['subscriptions'],
                )
            )
            if not len(self._subscriptions[symbol]['subscriptions']):
                del self._subscriptions[symbol]
        return self._websocket_client.unsubscribe_from_market_data(
            self._account.id, symbol, unsubscriptions, self._account.reliability
        )

    async def on_subscription_downgraded(
        self,
        instance_index: str,
        symbol: str,
        updates: Union[List[MarketDataSubscription], None] = None,
        unsubscriptions: Union[List[MarketDataUnsubscription], None] = None,
    ):
        """Invoked when subscription downgrade has occurred.

        Args:
            instance_index: Index of an account instance connected.
            symbol: Symbol to update subscriptions for.
            updates: Array of market data subscription to update.
            unsubscriptions: Array of subscriptions to cancel.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        if unsubscriptions and len(unsubscriptions):
            try:
                asyncio.create_task(self.unsubscribe_from_market_data(symbol, unsubscriptions))
            except Exception as err:
                message = (
                    f"{self._account.id}: failed do unsubscribe from market data on subscription downgraded"
                    f"{format_error(err)}"
                )

                if err.__class__.__name__ == "ValidationException":
                    self._logger.debug(message)
                else:
                    self._logger.error(message)

        if updates and len(updates):
            try:
                asyncio.create_task(self.subscribe_to_market_data(symbol, updates))
            except Exception as err:
                self._logger.error(
                    f"{self._account.id}: failed do unsubscribe from market data on subscription downgraded"
                    f"{format_error(err)}"
                )

    @property
    def subscribed_symbols(self) -> List[str]:
        """Returns list of the symbols connection is subscribed to.

        Returns:
            List of the symbols connection is subscribed to.
        """
        return list(self._subscriptions.keys())

    def subscriptions(self, symbol) -> List[MarketDataSubscription]:
        """Returns subscriptions for a symbol.

        Args:
            symbol: Symbol to retrieve subscriptions for.

        Returns:
            List of market data subscriptions for the symbol.
        """
        self._check_is_connection_active()
        return self._subscriptions.get(symbol, {}).get('subscriptions')

    @property
    def terminal_state(self) -> TerminalState:
        """Returns local copy of terminal state.

        Returns:
            Local copy of terminal state.
        """
        return self._terminal_state

    @property
    def history_storage(self) -> HistoryStorage:
        """Returns local history storage.

        Returns:
            Local history storage.
        """
        return self._history_storage

    async def on_connected(self, instance_index: str, replicas: int):
        """Invoked when connection to MetaTrader terminal established.

        Args:
            instance_index: Index of an account instance connected.
            replicas: Number of account replicas launched.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        key = random_id(32)
        state = self._get_state(instance_index)
        region = self.get_region(instance_index)
        self.cancel_refresh(region)
        await self._terminal_hash_manager.refresh_ignored_field_lists(region)
        state['shouldSynchronize'] = key
        state['synchronizationRetryIntervalInSeconds'] = 1
        state['synchronized'] = False
        asyncio.create_task(self._ensure_synchronized(instance_index, key))
        self._logger.debug(f'{self._account.id}:{instance_index}: connected to broker')

    async def on_disconnected(self, instance_index: str):
        """Invoked when connection to MetaTrader terminal terminated.

        Args:
            instance_index: Index of an account instance connected.

        Returns:
             A coroutine which resolves when the asynchronous event is processed.
        """
        state = self._get_state(instance_index)
        state['lastDisconnectedSynchronizationId'] = state['lastSynchronizationId']
        state['lastSynchronizationId'] = None
        state['shouldSynchronize'] = None
        state['synchronized'] = False
        state['disconnected'] = True
        instance_number = self.get_instance_number(instance_index)
        region = self.get_region(instance_index)
        instance = f'{region}:{instance_number}'
        if instance in self._refresh_market_data_subscription_sessions:
            del self._refresh_market_data_subscription_sessions[instance]
        if instance in self._refresh_market_data_subscription_timeouts:
            self._refresh_market_data_subscription_timeouts[instance].cancel()
            del self._refresh_market_data_subscription_timeouts[instance]
        if state['synchronizationTimeout']:
            state['synchronizationTimeout'].cancel()
            state['synchronizationTimeout'] = None
        if state['ensureSynchronizeTimeout']:
            state['ensureSynchronizeTimeout'].cancel()
            state['ensureSynchronizeTimeout'] = None
        self._logger.debug(f'{self._account.id}:{instance_index}: disconnected from broker')

    async def on_symbol_specifications_updated(
        self, instance_index: str, specifications: List[MetatraderSymbolSpecification], removed_symbols: List[str]
    ):
        """Invoked when a symbol specifications were updated.

        Args:
            instance_index: Index of account instance connected.
            specifications: Updated specifications.
            removed_symbols: Removed symbols.
        """
        self._schedule_synchronization_timeout(instance_index)

    async def on_positions_synchronized(self, instance_index: str, synchronization_id: str):
        """Invoked when position synchronization finished to indicate progress of an initial terminal state
        synchronization.

        Args:
            instance_index: Index of an account instance connected.
            synchronization_id: Synchronization request id.
        """
        self._schedule_synchronization_timeout(instance_index)

    async def on_pending_orders_synchronized(self, instance_index: str, synchronization_id: str):
        """Invoked when pending order synchronization finished to indicate progress of an initial terminal state
        synchronization.

        Args:
            instance_index: Index of an account instance connected.
            synchronization_id: Synchronization request id.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        self._schedule_synchronization_timeout(instance_index)

    async def on_deals_synchronized(self, instance_index: str, synchronization_id: str):
        """Invoked when a synchronization of history deals on a MetaTrader account have finished to indicate progress
        of an initial terminal state synchronization.

        Args:
            instance_index: Index of an account instance connected.
            synchronization_id: Synchronization request id.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        state = self._get_state(instance_index)
        state['dealsSynchronized'][synchronization_id] = True
        self._schedule_synchronization_timeout(instance_index)
        self._logger.debug(f'{self._account.id}:{instance_index}: finished synchronization {synchronization_id}')

    async def on_history_orders_synchronized(self, instance_index: str, synchronization_id: str):
        """Invoked when a synchronization of history orders on a MetaTrader account have finished to indicate progress
        of an initial terminal state synchronization.

        Args:
            instance_index: Index of an account instance connected.
            synchronization_id: Synchronization request id.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        state = self._get_state(instance_index)
        state['ordersSynchronized'][synchronization_id] = True
        self._schedule_synchronization_timeout(instance_index)

    async def on_reconnected(self, region: str, instance_number: int):
        """Invoked when connection to MetaApi websocket API restored after a disconnect.

        Args:
            region: Reconnected region.
            instance_number: Reconnected instance number.

        Returns:
            A coroutine which resolves when connection to MetaApi websocket API restored after a disconnect.
        """
        instance_template = f'{region}:{instance_number}'
        for key in list(
            filter(lambda key: key.startswith(f'{instance_template}:'), self._state_by_instance_index.keys())
        ):
            del self._state_by_instance_index[key]
        if instance_template in self._refresh_market_data_subscription_sessions:
            del self._refresh_market_data_subscription_sessions[instance_template]

        if instance_template in self._refresh_market_data_subscription_timeouts:
            self._refresh_market_data_subscription_timeouts[instance_template].cancel()
            del self._refresh_market_data_subscription_timeouts[instance_template]

    async def on_stream_closed(self, instance_index: str):
        """Invoked when a stream for an instance index is closed.

        Args:
            instance_index: Index of an account instance connected.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        if instance_index in self._state_by_instance_index:
            del self._state_by_instance_index[instance_index]

    async def on_synchronization_started(
        self,
        instance_index: str,
        specifications_hash=None,
        positions_hash=None,
        orders_hash=None,
        synchronization_id: str = None,
    ):
        """Invoked when MetaTrader terminal state synchronization is started.

        Args:
            instance_index: Index of an account instance connected.
            specifications_hash: Specifications hash.
            positions_hash: Positions hash.
            orders_hash: Orders hash.
            synchronization_id: Synchronization id.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        self._logger.debug(f'{self._account.id}:{instance_index}: starting synchronization ${synchronization_id}')
        instance_number = self.get_instance_number(instance_index)
        region = self.get_region(instance_index)
        instance = f'{region}:{instance_number}'
        account_id = self._account.account_regions[region]
        if instance in self._refresh_market_data_subscription_sessions:
            del self._refresh_market_data_subscription_sessions[instance]
        session_id = random_id(32)
        self._refresh_market_data_subscription_sessions[instance] = session_id
        if instance in self._refresh_market_data_subscription_timeouts:
            self._refresh_market_data_subscription_timeouts[instance].cancel()
            del self._refresh_market_data_subscription_timeouts[instance]
        await self._refresh_market_data_subscriptions(account_id, instance_number, session_id)
        self._schedule_synchronization_timeout(instance_index)
        state = self._get_state(instance_index)
        if state and not self._closed:
            state['lastSynchronizationId'] = synchronization_id

    async def on_unsubscribe_region(self, region: str):
        """Invoked when account region has been unsubscribed.

        Args:
            region: Account region unsubscribed.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        for instance in list(
            filter(
                lambda instance: instance.startswith(f'{region}:'),
                self._refresh_market_data_subscription_timeouts.keys(),
            )
        ):
            self._refresh_market_data_subscription_timeouts[instance].cancel()
            del self._refresh_market_data_subscription_timeouts[instance]
            del self._refresh_market_data_subscription_sessions[instance]
        for instance in list(
            filter(lambda instance: instance.startswith(f'{region}:'), self._state_by_instance_index.keys())
        ):
            del self._state_by_instance_index[instance]

    async def is_synchronized(self, instance_index: str, synchronization_id: str = None) -> bool:
        """Returns flag indicating status of state synchronization with MetaTrader terminal.

        Args:
            instance_index: Index of an account instance connected.
            synchronization_id: Optional synchronization request id, last synchronization request id will be used.

        Returns:
            A coroutine resolving with a flag indicating status of state synchronization with MetaTrader terminal.
        """

        def reducer_func(acc, s: MetaApiConnectionDict):
            if instance_index is not None and s['instanceIndex'] != instance_index:
                return acc
            check_synchronization_id = synchronization_id or s['lastSynchronizationId']
            synchronized = (
                check_synchronization_id in s['ordersSynchronized']
                and bool(s['ordersSynchronized'][check_synchronization_id])
                and check_synchronization_id in s['dealsSynchronized']
                and bool(s['dealsSynchronized'][check_synchronization_id])
            )
            return acc or synchronized

        return (
            reduce(reducer_func, self._state_by_instance_index.values(), False)
            if len(self._state_by_instance_index.values())
            else False
        )

    async def wait_synchronized(self, opts: SynchronizationOptions = None):
        """Waits until synchronization to MetaTrader terminal is completed.

        Args:
            opts: Synchronization options.

        Returns:
            A coroutine which resolves when synchronization to MetaTrader terminal is completed.

        Raises:
            TimeoutException: If application failed to synchronize with the terminal within timeout allowed.
        """
        self._check_is_connection_active()
        start_time = datetime.now()
        opts = opts or {}
        instance_index = opts.get('instanceIndex')
        synchronization_id = opts.get('synchronizationId')
        timeout_in_seconds = opts.get('timeoutInSeconds', 300)
        interval_in_milliseconds = opts.get('intervalInMilliseconds', 100)
        application_pattern = opts.get('applicationPattern', 'RPC')
        synchronized = await self.is_synchronized(instance_index, synchronization_id)
        while not synchronized and (start_time + timedelta(seconds=timeout_in_seconds) > datetime.now()):
            await asyncio.sleep(interval_in_milliseconds / 1000)
            synchronized = await self.is_synchronized(instance_index, synchronization_id)
        state = None
        if instance_index is None:
            for s in self._state_by_instance_index.values():
                if await self.is_synchronized(s['instanceIndex'], synchronization_id):
                    state = s
                    instance_index = s['instanceIndex']
        else:
            state = next((s for s in self._state_by_instance_index if s['instanceIndex'] == instance_index), None)
        if not synchronized:
            raise TimeoutException(
                'Timed out waiting for MetaApi to synchronize to MetaTrader account '
                + self._account.id
                + ', synchronization id '
                + (
                    synchronization_id
                    or (bool(state) and state['lastSynchronizationId'])
                    or (bool(state) and state['lastDisconnectedSynchronizationId'])
                    or 'None'
                )
            )
        time_left_in_seconds = max(0, timeout_in_seconds - (datetime.now() - start_time).total_seconds())
        region = self.get_region(state['instanceIndex'])
        account_id = self._account.account_regions[region]
        await self._websocket_client.wait_synchronized(
            account_id, self.get_instance_number(instance_index), application_pattern, time_left_in_seconds
        )

    async def close(self, instance_id: str):
        """Closes the connection. The instance of the class should no longer be used after this method is invoked.

        Args:
            instance_id: Connection instance id.
        """
        if self._opened:
            self._opened_instances = list(filter(lambda id: id != instance_id, self._opened_instances))
            if not len(self._opened_instances) and not self._closed:
                self._logger.debug(f'{self._account.id}: Closing connection')
                for state in self._state_by_instance_index.values():
                    if state.get('synchronizationTimeout'):
                        state['synchronizationTimeout'].cancel()
                self._state_by_instance_index = {}
                await self._connection_registry.remove_streaming(self._account)
                self._terminal_state.close()
                account_regions = self._account.account_regions
                self._websocket_client.remove_synchronization_listener(self._account.id, self)
                self._websocket_client.remove_synchronization_listener(self._account.id, self._terminal_state)
                self._websocket_client.remove_synchronization_listener(self._account.id, self._history_storage)
                self._websocket_client.remove_synchronization_listener(self._account.id, self._health_monitor)
                self._websocket_client.remove_reconnect_listener(self)
                self._health_monitor.stop()
                self._refresh_market_data_subscription_sessions = {}
                for instance in list(self._refresh_market_data_subscription_timeouts.keys()):
                    self._refresh_market_data_subscription_timeouts[instance].cancel()
                self._refresh_market_data_subscription_timeouts = {}
                for replica_id in account_regions.values():
                    self._websocket_client.remove_account_cache(replica_id)
                self._closed = True
                self._logger.debug(f'{self._account.id}: Closed connection')

    @property
    def synchronized(self) -> bool:
        """Returns synchronization status.

        Returns:
            Synchronization status.
        """
        return True in list(map(lambda s: s['synchronized'], self._state_by_instance_index.values()))

    @property
    def health_monitor(self) -> ConnectionHealthMonitor:
        """Returns connection health monitor instance.

        Returns:
            Connection health monitor instance.
        """
        return self._health_monitor

    async def _refresh_market_data_subscriptions(self, account_id: str, instance_number: int, session: str):
        region = self._websocket_client.get_account_region(account_id)
        instance = f'{region}:{instance_number}'
        try:
            if (
                instance in self._refresh_market_data_subscription_sessions
                and self._refresh_market_data_subscription_sessions[instance] == session
            ):
                subscriptions_list = []
                for key in self._subscriptions.keys():
                    subscriptions = self.subscriptions(key)
                    subscriptions_item = {'symbol': key}
                    if subscriptions is not None:
                        subscriptions_item['subscriptions'] = subscriptions
                    subscriptions_list.append(subscriptions_item)
                await self._websocket_client.refresh_market_data_subscriptions(
                    account_id, instance_number, subscriptions_list
                )
        except Exception as err:
            self._logger.error(
                f'Error refreshing market data subscriptions job for account {self._account.id} '
                f'{instance} ' + string_format_error(err)
            )
        finally:

            async def refresh_market_data_subscriptions_job():
                await asyncio.sleep(
                    uniform(self._min_subscription_refresh_interval, self._max_subscription_refresh_interval)
                )
                await self._refresh_market_data_subscriptions(account_id, instance_number, session)

            if (
                instance in self._refresh_market_data_subscription_sessions
                and self._refresh_market_data_subscription_sessions[instance] == session
            ):
                self._refresh_market_data_subscription_timeouts[instance] = asyncio.create_task(
                    refresh_market_data_subscriptions_job()
                )

    async def _ensure_synchronized(self, instance_index: str, key):
        state = self._get_state(instance_index)
        if state and not self._closed:
            try:
                synchronization_result = await self.synchronize(instance_index)
                if synchronization_result:
                    state['synchronized'] = True
                    state['synchronizationRetryIntervalInSeconds'] = 1
                    state['ensureSynchronizeTimeout'] = None
                self._schedule_synchronization_timeout(instance_index)
            except Exception as err:
                msg = f'MetaApi websocket client for account {self.account.id}:{str(instance_index)}' \
                    f' failed to synchronize ' + string_format_error(err)
                if len(self._latency_service.get_synchronized_account_instances(self.account.id)):
                    self._logger.debug(msg)
                else:
                    self._logger.error(msg)
                if state['shouldSynchronize'] == key:
                    if state['ensureSynchronizeTimeout']:
                        state['ensureSynchronizeTimeout'].cancel()

                    async def restart_ensure_sync():
                        await asyncio.sleep(state['synchronizationRetryIntervalInSeconds'])
                        await self._ensure_synchronized(instance_index, key)

                    state['ensureSynchronizeTimeout'] = asyncio.create_task(restart_ensure_sync())
                    state['synchronizationRetryIntervalInSeconds'] = min(
                        state['synchronizationRetryIntervalInSeconds'] * 2, 300
                    )

    def _get_state(self, instance_index: str) -> MetaApiConnectionDict:
        if instance_index not in self._state_by_instance_index:
            self._state_by_instance_index[instance_index] = {
                'instanceIndex': instance_index,
                'ordersSynchronized': {},
                'dealsSynchronized': {},
                'shouldSynchronize': None,
                'synchronizationRetryIntervalInSeconds': 1,
                'synchronized': False,
                'lastDisconnectedSynchronizationId': None,
                'lastSynchronizationId': None,
                'disconnected': False,
                'synchronizationTimeout': None,
                'ensureSynchronizeTimeout': None,
            }
        return self._state_by_instance_index[instance_index]

    def _schedule_synchronization_timeout(self, instance_index: str):
        state = self._get_state(instance_index)
        if state and state['shouldSynchronize'] and not self._closed:
            if state['synchronizationTimeout']:
                state['synchronizationTimeout'].cancel()

            async def _check_timed_out():
                await asyncio.sleep(2 * 60)
                self._check_synchronization_timed_out(instance_index)

            state['synchronizationTimeout'] = asyncio.create_task(_check_timed_out())
            self._logger.debug(f"{self._account.id}:{instance_index}: scheduled synchronization timeout")

    def _check_synchronization_timed_out(self, instance_index: str):
        self._logger.debug(f"{self._account.id}:{instance_index}: checking if synchronization timed out out")
        state = self._get_state(instance_index)
        if state and not self._closed:
            synchronization_id = state['lastSynchronizationId']
            synchronized = state['dealsSynchronized'].get(synchronization_id)
            if not synchronized and synchronization_id and state['shouldSynchronize']:
                self._logger.warn(
                    f'{self._account.id}:{instance_index}: resynchronized since latest '
                    + f'synchronization {synchronization_id} did not finish in time'
                )
                asyncio.create_task(self._ensure_synchronized(instance_index, state['shouldSynchronize']))
