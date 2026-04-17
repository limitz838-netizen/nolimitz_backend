import asyncio
import json
import math
import re
from contextlib import suppress
from copy import copy
from datetime import datetime, timedelta
from random import random
from typing import Coroutine, List, Dict, Callable, TypedDict, Optional

import socketio

from .latency_listener import LatencyListener
from .latency_service import LatencyService
from .not_connected_exception import NotConnectedException
from .not_synchronized_exception import NotSynchronizedException
from .packet_logger import PacketLogger
from .packet_orderer import PacketOrderer
from .reconnect_listener import ReconnectListener
from .subscription_manager import SubscriptionManager
from .synchronization_listener import SynchronizationListener
from .synchronization_throttler import SynchronizationThrottler
from .trade_exception import TradeException
from ..error_handler import (
    ValidationException,
    NotFoundException,
    InternalException,
    UnauthorizedException,
    TooManyRequestsException,
    ForbiddenException,
)
from ..options_validator import OptionsValidator
from ..timeout_exception import TimeoutException
from ...logger import LoggerManager, SocketLogger
from .packet_logger import PacketLoggerOpts
from .synchronization_throttler import SynchronizationThrottlerOpts
from ...metaapi.models import (
    MetatraderHistoryOrders,
    MetatraderDeals,
    date,
    random_id,
    MetatraderSymbolSpecification,
    MetatraderTradeResponse,
    MetatraderSymbolPrice,
    MetatraderAccountInformation,
    MetatraderPosition,
    MetatraderOrder,
    format_date,
    MarketDataSubscription,
    MarketDataUnsubscription,
    MetatraderCandle,
    MetatraderTick,
    MetatraderBook,
    ServerTime,
    string_format_error,
    format_error,
    promise_any,
    Margin,
    MarginOrder,
    GetAccountInformationOptions,
    GetPositionsOptions,
    GetPositionOptions,
    GetOrdersOptions,
    GetOrderOptions,
    RefreshedQuotes,
    MetatraderTrade,
    RetryOpts
)


class MetaApiWebsocketClientOptions(TypedDict, total=False):
    """MetaApi websocket client options"""

    application: Optional[str]
    """Application id."""
    domain: Optional[str]
    """Domain to connect to, default is agiliumtrade.agiliumtrade.ai."""
    region: Optional[str]
    """Region to connect to."""
    requestTimeout: Optional[float]
    """Timeout for socket requests in seconds."""
    connectTimeout: Optional[float]
    """Timeout for connecting to server in seconds."""
    packetLogger: Optional[PacketLoggerOpts]
    """Packet logger options."""
    packetOrderingTimeout: Optional[float]
    """Packet ordering timeout in seconds."""
    synchronizationThrottler: Optional[SynchronizationThrottlerOpts]
    """Options for synchronization throttler."""
    retryOpts: Optional[RetryOpts]
    """Options for request retries."""
    useSharedClientApi: Optional[bool]
    """Option to use a shared server."""
    unsubscribeThrottlingIntervalInSeconds: Optional[float]
    """A timeout in seconds for throttling repeat unsubscribe
    requests when synchronization packets still arrive after unsubscription, default is 10 seconds"""
    enableSocketioDebugger: Optional[bool]
    """Option to enable debug mode."""
    websocketLogPath: Optional[str]
    """You can record detailed web socket logs to diagnose your problem to a log file. In order to do so,
    please specify the path to socket.io log filename in this setting"""
    disableInternalJobs: bool
    """Whether to not run internal interval jobs. Used for tests only."""


class MetaApiWebsocketClient:
    """MetaApi websocket API client (see https://metaapi.cloud/docs/client/websocket/overview/)"""

    def __init__(self, meta_api, domain_client, token: str, opts: MetaApiWebsocketClientOptions = None):
        """Initializes MetaApi websocket API client instance.

        Args:
            meta_api: MetaApi instance.
            domain_client: Domain client.
            token: Authorization token.
            opts: Websocket client options.
        """
        validator = OptionsValidator()
        opts = opts or {}
        opts['packetOrderingTimeout'] = validator.validate_non_zero(
            opts.get('packetOrderingTimeout'), 60, 'packetOrderingTimeout'
        )
        opts['synchronizationThrottler'] = opts.get('synchronizationThrottler', {})
        self._domain_client = domain_client
        self._application = opts.get('application', 'MetaApi')
        self._domain = opts.get('domain', 'agiliumtrade.agiliumtrade.ai')
        self._region = opts.get('region')
        self._hostname = 'mt-client-api-v1'
        self._meta_api = meta_api
        self._url = None
        self._request_timeout = validator.validate_non_zero(opts.get('requestTimeout'), 60, 'requestTimeout')
        self._connect_timeout = validator.validate_non_zero(opts.get('connectTimeout'), 60, 'connectTimeout')
        retry_opts: RetryOpts = opts.get('retryOpts', {})
        self._retries = validator.validate_number(retry_opts.get('retries'), 5, 'retries')
        self._min_retry_delay_in_seconds = validator.validate_non_zero(
            retry_opts.get('minDelayInSeconds'), 1, 'minDelayInSeconds'
        )
        self._max_retry_delay_in_seconds = validator.validate_non_zero(
            retry_opts.get('maxDelayInSeconds'), 30, 'maxDelayInSeconds'
        )
        self._max_accounts_per_instance = 100
        self._subscribe_cooldown_in_seconds = validator.validate_non_zero(
            retry_opts.get('subscribeCooldownInSeconds'), 600, 'subscribeCooldownInSeconds'
        )
        self._sequential_event_processing = True
        self._use_shared_client_api = validator.validate_boolean(
            opts.get('useSharedClientApi'), False, 'useSharedClientApi'
        )
        self._enable_socketio_debugger = validator.validate_boolean(
            opts.get('enableSocketioDebugger'), False, 'enableSocketioDebugger'
        )
        self._unsubscribe_throttling_interval = validator.validate_non_zero(
            opts.get('unsubscribeThrottlingIntervalInSeconds'), 10, 'unsubscribeThrottlingIntervalInSeconds'
        )
        self._socket_minimum_reconnect_timeout = 0.5
        self._latency_service = LatencyService(self, token, self._connect_timeout)
        self._token = token
        self._synchronization_listeners = {}
        self._latency_listeners = []
        self._reconnect_listeners = []
        self._connected_hosts = {}
        self._socket_instances = {}
        self._socket_instances_by_accounts = {}
        self._regions_by_accounts = {}
        self._accounts_by_replica_id = {}
        self._account_replicas = {}
        self._synchronization_throttler_options = opts['synchronizationThrottler']
        self._subscription_manager = SubscriptionManager(self, meta_api)
        self._packet_orderer = PacketOrderer(self, opts['packetOrderingTimeout'])
        self._packet_orderer.start()
        self._status_timers = {}
        self._event_queues = {}
        self._synchronization_flags = {}
        self._synchronization_id_by_instance = {}
        self._subscribe_lock = None
        self._first_connect = True
        self._last_requests_time = {}
        self._logger = LoggerManager.get_logger('MetaApiWebsocketClient')
        self._synchronization_hashes = {}
        self._update_events = {}
        if 'packetLogger' in opts and 'enabled' in opts['packetLogger'] and opts['packetLogger']['enabled']:
            self._packet_logger = PacketLogger(opts['packetLogger'])
            self._packet_logger.start()
        else:
            self._packet_logger = None
        enable_socketio_debugger = opts.get('enableSocketioDebugger', False)
        if enable_socketio_debugger:
            self._socket_io_logger = SocketLogger(opts['websocketLogPath'])
            self._socket_io_logger.start()
        else:
            self._socket_io_logger = None

        if 'disableInternalJobs' not in opts or not opts['disableInternalJobs']:

            async def clear_account_cache_task():
                while True:
                    await asyncio.sleep(30 * 60)
                    self._clear_account_cache_job()

            async def clear_inactive_sync_data_job():
                while True:
                    await asyncio.sleep(5 * 60)
                    self._clear_inactive_sync_data_job()

            self._clearAccountCacheInterval = asyncio.create_task(clear_account_cache_task())
            self._clearInactiveSyncDataInterval = asyncio.create_task(clear_inactive_sync_data_job())

    async def on_out_of_order_packet(
        self,
        account_id: str,
        instance_index: int,
        expected_sequence_number: int,
        actual_sequence_number: int,
        packet: Dict,
        received_at: datetime,
    ):
        """Restarts the account synchronization process on an out-of-order packet.

        Args:
            account_id: Account id.
            instance_index: Instance index.
            expected_sequence_number: Expected s/n.
            actual_sequence_number: Actual s/n.
            packet: Packet data.
            received_at: Time the packet was received at.
        """
        primary_account_id = self._accounts_by_replica_id[account_id]
        if self._subscription_manager.is_subscription_active(account_id):
            msg = f'MetaApi websocket client received an out of order packet ' \
                f'type {packet["type"]} for account id {account_id}:{instance_index}. Expected s/n ' \
                f'{expected_sequence_number} does not match the actual of {actual_sequence_number}'
            if len(self._latency_service.get_synchronized_account_instances(primary_account_id)):
                self._logger.debug(msg)
            else:
                self._logger.error(msg)
            self.ensure_subscribe(account_id, instance_index)

    def set_url(self, url: str):
        """Patch server URL for use in unit tests

        Args:
            url: Patched server URL.
        """
        self._url = url

    @property
    def region(self):
        """Websocket client predefined region.

        Returns:
            Predefined region.
        """
        return self._region

    @property
    def socket_instances(self):
        """Returns the list of socket instance dictionaries."""
        return self._socket_instances

    @property
    def socket_instances_by_accounts(self):
        """Returns the dictionary of socket instances by account ids"""
        return self._socket_instances_by_accounts

    @property
    def account_replicas(self) -> dict:
        """Returns the dictionary of account replicas by region.

        Returns:
            Dictionary of account replicas by region.
        """
        return self._account_replicas

    @property
    def accounts_by_replica_id(self) -> dict:
        """Returns the dictionary of primary account ids by replica ids.

        Returns:
            Dictionary of primary account ids by replica ids.
        """
        return self._accounts_by_replica_id

    @property
    def latency_service(self) -> LatencyService:
        """Returns latency service.

        Returns:
           Latency service.
        """
        return self._latency_service

    def subscribed_account_ids(
        self, instance_number: int, socket_instance_index: int = None, region: str = None
    ) -> List[str]:
        """Returns the list of subscribed account ids.

        Args:
            instance_number: Instance index number.
            socket_instance_index: Socket instance index.
            region: Server region.
        """
        connected_ids = []
        if instance_number in self._socket_instances_by_accounts:
            for instance_id in self._connected_hosts.keys():
                account_id = instance_id.split(':')[0]
                account_region = self.get_account_region(account_id)
                if (
                    account_id not in connected_ids
                    and account_id in self._socket_instances_by_accounts[instance_number]
                    and (
                        self._socket_instances_by_accounts[instance_number][account_id] == socket_instance_index
                        or socket_instance_index is None
                    )
                    and account_region == region
                ):
                    connected_ids.append(account_id)
        return connected_ids

    def connected(self, instance_number: int, socket_instance_index: int, region: str) -> bool:
        """Returns websocket client connection status.

        Args:
            instance_number: Instance index number.
            socket_instance_index: Socket instance index.
            region: Server region.
        """
        instance = (
            self._socket_instances[region][instance_number][socket_instance_index]
            if region in self._socket_instances
            and instance_number in self._socket_instances[region]
            and len(self._socket_instances[region][instance_number]) > socket_instance_index
            else None
        )
        return instance['socket'].connected if instance else False

    def get_assigned_accounts(self, instance_number: int, socket_instance_index: int, region: str):
        """Returns list of accounts assigned to instance.

        Args:
            instance_number: Instance index number.
            socket_instance_index: Socket instance index.
            region: Server region.
        """
        account_ids = []
        for key in self._socket_instances_by_accounts[instance_number].keys():
            account_region = self.get_account_region(key)
            if (
                account_region == region
                and self._socket_instances_by_accounts[instance_number][key] == socket_instance_index
            ):
                account_ids.append(key)
        return account_ids

    def get_account_region(self, account_id: str) -> str or None:
        """Returns account region by id.

        Args:
            account_id: Account id.

        Returns:
            Account region
        """
        return self._regions_by_accounts[account_id]['region'] if account_id in self._regions_by_accounts else None

    def add_account_cache(self, account_id: str, replicas: dict):
        """Adds account region info.

        Args:
            account_id: Account id.
            replicas: Account replicas.
        """
        self._account_replicas[account_id] = replicas
        for region in replicas.keys():
            replica_id = replicas[region]
            if replica_id not in self._regions_by_accounts:
                self._regions_by_accounts[replica_id] = {
                    'region': region,
                    'connections': 1,
                    'lastUsed': datetime.now().timestamp(),
                }
            else:
                self._regions_by_accounts[account_id]['connections'] += 1
            self._accounts_by_replica_id[replica_id] = account_id

    def update_account_cache(self, account_id: str, replicas: Dict):
        """Updates account cache info.

        Args:
            account_id: Account id.
            replicas: Account replicas.
        """
        old_replicas = self._account_replicas.get(account_id)

        if old_replicas:
            connection_count = self._regions_by_accounts[account_id]['connections']

            for region in old_replicas.keys():
                replica_id = replicas.get(region)
                if replica_id:
                    if replica_id in self._accounts_by_replica_id:
                        del self._accounts_by_replica_id[replica_id]
                    if replica_id in self._regions_by_accounts:
                        del self._regions_by_accounts[replica_id]

            self._account_replicas[account_id] = replicas

            for region in replicas.keys():
                replica_id = replicas[region]
                self._regions_by_accounts[replica_id] = {
                    'region': region,
                    'connections': connection_count,
                    'lastUsed': datetime.now().timestamp(),
                }
                self._accounts_by_replica_id[replica_id] = account_id

            self._logger.debug(f"{account_id}: updated account cache")

    def remove_account_cache(self, account_id: str):
        """Removes account region info.

        Args:
            account_id: Account id.
        """
        if account_id in self._regions_by_accounts:
            if self._regions_by_accounts[account_id]['connections'] > 0:
                self._regions_by_accounts[account_id]['connections'] -= 1

    async def lock_socket_instance(self, instance_number: int, socket_instance_index: int, region: str, metadata: Dict):
        """Locks subscription for a socket instance based on TooManyRequestsException metadata.

        Args:
            instance_number: Instance index number.
            socket_instance_index: Socket instance index.
            region: Server region.
            metadata: TooManyRequestsException metadata.
        """
        if metadata['type'] == 'LIMIT_ACCOUNT_SUBSCRIPTIONS_PER_USER':
            self._subscribe_lock = {
                'recommendedRetryTime': metadata['recommendedRetryTime'],
                'lockedAtAccounts': len(self.subscribed_account_ids(instance_number, None, region)),
                'lockedAtTime': datetime.now().timestamp(),
            }
        else:
            subscribed_accounts = self.subscribed_account_ids(instance_number, socket_instance_index, region)
            if len(subscribed_accounts) == 0:
                await self._reconnect(instance_number, socket_instance_index, region)
            else:
                instance = self.socket_instances[region][instance_number][socket_instance_index]
                instance['subscribeLock'] = {
                    'recommendedRetryTime': metadata['recommendedRetryTime'],
                    'type': metadata['type'],
                    'lockedAtAccounts': len(subscribed_accounts),
                }

    async def connect(self, instance_number: int, region: str) -> asyncio.Future:
        """Connects to MetaApi server via socket.io protocol

        Args:
            instance_number: Instance index number.
            region: Server region.

        Returns:
            A coroutine which resolves when connection is established.
        """
        if self._region and region != self._region:
            raise ValidationException(f"Trying to connect to {region} region, but configured with {self._region}")
        self._socket_instances[region] = self._socket_instances.get(region, {})
        self._socket_instances[region][instance_number] = self.socket_instances[region].get(instance_number, [])
        socket_instance_index = len(self._socket_instances[region][instance_number])
        instance = {
            'id': socket_instance_index,
            'reconnectWaitTime': self._socket_minimum_reconnect_timeout,
            'connected': False,
            'requestResolves': {},
            'resolved': False,
            'connectResult': asyncio.Future(),
            'sessionId': random_id(),
            'isReconnecting': False,
            'socket': socketio.AsyncClient(
                reconnection=False, request_timeout=self._request_timeout,
                engineio_logger=self._socket_io_logger if self._socket_io_logger else False,
                logger=self._socket_io_logger if self._socket_io_logger else False
            ),
            'synchronizationThrottler': SynchronizationThrottler(
                self, socket_instance_index, instance_number, region, self._synchronization_throttler_options
            ),
            'subscribeLock': None,
            'instanceNumber': instance_number,
        }
        instance['synchronizationThrottler'].start()
        socket_instance = instance['socket']
        self._socket_instances[region][instance_number].append(instance)
        instance['connected'] = True

        @socket_instance.on('connect')
        async def on_connect():
            self._logger.info(f'{region}:{instance_number}: MetaApi websocket client connected to the MetaApi server')
            instance['reconnectWaitTime'] = self._socket_minimum_reconnect_timeout
            if not instance['resolved']:
                instance['resolved'] = True
                instance['connectResult'].set_result(None)

            if not instance['connected']:
                await instance['socket'].disconnect()

        @socket_instance.on('connect_error')
        def on_connect_error(err):
            self._logger.error(
                f'{region}:{instance_number}: MetaApi websocket client connection error ' + string_format_error(err)
            )
            if not instance['resolved']:
                instance['resolved'] = True
                instance['connectResult'].set_exception(Exception(err))

        @socket_instance.on('connect_timeout')
        def on_connect_timeout(timeout):
            self._logger.error(f'{region}:{instance_number}: MetaApi websocket client connection timeout')
            if not instance['resolved']:
                instance['resolved'] = True
                instance['connectResult'].set_exception(
                    TimeoutException('MetaApi websocket client connection timed out')
                )

        @socket_instance.on('disconnect')
        async def on_disconnect():
            instance['synchronizationThrottler'].on_disconnect()
            self._logger.info(
                f'{region}:{instance_number}: MetaApi websocket client disconnected from the' ' MetaApi server'
            )
            await self._reconnect(instance_number, instance['id'], region)

        @socket_instance.on('error')
        async def on_error(err):
            self._logger.error(
                f'{region}:{instance_number}: MetaApi websocket client error ' + string_format_error(err)
            )
            await self._reconnect(instance_number, instance['id'], region)

        @socket_instance.on('response')
        async def on_response(data):
            if isinstance(data, str):
                data = json.loads(data)
            self._logger.debug(
                f"{data['accountId']}: Response received: "
                + json.dumps({'requestId': data['requestId'], 'timestamps': data.get('timestamps')})
            )
            if data['requestId'] in instance['requestResolves']:
                request_resolve = instance['requestResolves'][data['requestId']]
                del instance['requestResolves'][data['requestId']]
            else:
                request_resolve = asyncio.Future()
            self._convert_iso_time_to_date(data)
            if not request_resolve.done():
                request_resolve.set_result(data)
            if 'timestamps' in data and hasattr(request_resolve, 'type'):
                data['timestamps']['clientProcessingFinished'] = datetime.now()
                for listener in self._latency_listeners:
                    try:
                        if request_resolve.type == 'trade':
                            await listener.on_trade(data['accountId'], data['timestamps'])
                        else:
                            await listener.on_response(data['accountId'], request_resolve.type, data['timestamps'])
                    except Exception as error:
                        self._logger.error(
                            f"Failed to process on_response event for account {data['accountId']}, "
                            f"request type {request_resolve.type} {string_format_error(error)}"
                        )

        @socket_instance.on('processingError')
        def on_processing_error(data):
            if data['requestId'] in instance['requestResolves']:
                request_resolve = instance['requestResolves'][data['requestId']]
                del instance['requestResolves'][data['requestId']]
                if not request_resolve.done():
                    request_resolve.set_exception(self._convert_error(data))

        @socket_instance.on('synchronization')
        async def on_synchronization(data):
            if isinstance(data, str):
                data = json.loads(data)
            packet_info = {
                'type': data['type'],
                'sequenceNumber': data.get('sequenceNumber'),
                'sequenceTimestamp': data.get('sequenceTimestamp'),
                'synchronizationId': data.get('synchronizationId'),
                'application': data.get('application'),
                'host': data.get('host'),
                'specificationsUpdated': data.get('specificationsUpdated'),
                'positionsUpdated': data.get('positionsUpdated'),
                'ordersUpdated': data.get('ordersUpdated'),
                'specifications': len(data['specifications']) if 'specifications' in data else None,
            }
            if 'instanceIndex' in data and data['instanceIndex'] != instance_number:
                self._logger.debug(
                    f'{data["accountId"]}:{data["instanceIndex"]}: received packet with wrong '
                    + f'instance index via a socket with instance number of {instance_number}, data'
                    + f'{json.dumps(packet_info)}'
                )
                return
            if data['accountId'] not in self._regions_by_accounts:
                self._regions_by_accounts[data['accountId']] = {
                    'region': region,
                    'connections': 0,
                    'lastUsed': datetime.now().timestamp(),
                }
            self._logger.debug(
                f"{data['accountId']}:{data.get('instanceIndex', 0)}: "
                f"Sync packet received: "
                + json.dumps(packet_info)
                + ', active listeners: '
                + str(
                    len(self._synchronization_listeners[data['accountId']])
                    if data['accountId'] in self._synchronization_listeners
                    else 0
                )
            )
            active_synchronization_ids = instance['synchronizationThrottler'].active_synchronization_ids
            if ('synchronizationId' not in data) or (data['synchronizationId'] in active_synchronization_ids):
                if self._packet_logger:
                    self._packet_logger.log_packet(data)
                self._convert_iso_time_to_date(data)
                ignored_packet_types = ['disconnected', 'status', 'keepalive']
                if (
                    not self._subscription_manager.is_subscription_active(data['accountId'])
                    and data['type'] not in ignored_packet_types
                ):
                    self._logger.debug(
                        f'{data["accountId"]}: Packet arrived to inactive connection, attempting '
                        f'unsubscribe, packet: {data["type"]}'
                    )
                    if self._throttle_request(
                        'unsubscribe', data['accountId'], instance_number, self._unsubscribe_throttling_interval
                    ):

                        async def unsubscribe():
                            try:
                                await self.unsubscribe(data['accountId'])
                            except Exception as err:
                                self._logger.warn(
                                    f'{data["accountId"]}:' f'{data.get("instanceIndex", 0)}: ' 'failed to unsubscribe',
                                    format_error(err),
                                )

                        asyncio.create_task(unsubscribe())
                    return
            else:
                data['type'] = 'noop'
            self.queue_packet(data)

        while not socket_instance.connected:
            with suppress(Exception):
                client_id = "{:01.10f}".format(random())
                server_url = await self._get_server_url(instance_number, socket_instance_index, region)
                url = f'{server_url}?auth-token={self._token}&clientId={client_id}&protocol=3'
                instance['sessionId'] = random_id()
                await asyncio.wait_for(
                    socket_instance.connect(url, socketio_path='ws', headers={'Client-Id': client_id}),
                    timeout=self._connect_timeout,
                )

        return instance['connectResult']

    async def close(self):
        """Closes connection to MetaApi server"""
        for region in self._socket_instances:
            for instance_number in self._socket_instances[region]:
                for instance in self._socket_instances[region][instance_number]:
                    if instance['connected']:
                        instance['connected'] = False
                        await instance['socket'].disconnect()
                        for request_resolve in instance['requestResolves']:
                            if not instance['requestResolves'][request_resolve].done():
                                instance['requestResolves'][request_resolve].set_exception(
                                    Exception('MetaApi connection closed')
                                )
                        instance['requestResolves'] = {}

                    self._socket_instances_by_accounts[instance_number] = {}
                    self._socket_instances[region][instance_number] = []

        self._synchronization_listeners = {}
        self._latency_listeners = []
        self._packet_orderer.stop()
        if self._packet_logger:
            self._packet_logger.stop()
        if self._socket_io_logger:
            self._socket_io_logger.stop()

    def stop(self):
        """Stops the client."""
        self._clearAccountCacheInterval.cancel()
        self._clearInactiveSyncDataInterval.cancel()
        self._latency_service.stop()

    async def get_account_information(
        self, account_id: str, options: GetAccountInformationOptions = None
    ) -> 'asyncio.Future[MetatraderAccountInformation]':
        """Returns account information for a specified MetaTrader account.

        Args:
            account_id: Id of the MetaTrader account to return information for.
            options: Additional request options.

        Returns:
            A coroutine resolving with account information.
        """
        if options is None:
            options = {}
        response = await self.rpc_request(
            account_id, {'application': 'RPC', 'type': 'getAccountInformation', **options}
        )
        return response['accountInformation']

    async def get_positions(
        self, account_id: str, options: GetPositionsOptions = None
    ) -> 'asyncio.Future[List[MetatraderPosition]]':
        """Returns positions for a specified MetaTrader account.

        Args:
            account_id: Id of the MetaTrader account to return information for.
            options: Additional request options.

        Returns:
            A coroutine resolving with array of open positions.
        """
        if options is None:
            options = {}
        response = await self.rpc_request(account_id, {'application': 'RPC', 'type': 'getPositions', **options})
        return response.get('positions')

    async def get_position(
        self, account_id: str, position_id: str, options: GetPositionOptions = None
    ) -> 'asyncio.Future[MetatraderPosition]':
        """Returns specific position for a MetaTrader account.

        Args:
            account_id: Id of the MetaTrader account to return information for.
            position_id: Position id.
            options: Additional request options.

        Returns:
            A coroutine resolving with MetaTrader position found.
        """
        if options is None:
            options = {}
        response = await self.rpc_request(
            account_id, {'application': 'RPC', 'type': 'getPosition', 'positionId': position_id, **options}
        )
        return response.get('position')

    async def get_orders(
        self, account_id: str, options: GetOrdersOptions = None
    ) -> 'asyncio.Future[List[MetatraderOrder]]':
        """Returns open orders for a specified MetaTrader account.

        Args:
            account_id: Id of the MetaTrader account to return information for.
            options: Additional request options.

        Returns:
            A coroutine resolving with open MetaTrader orders.
        """
        if options is None:
            options = {}
        response = await self.rpc_request(account_id, {'application': 'RPC', 'type': 'getOrders', **options})
        return response.get('orders')

    async def get_order(
        self, account_id: str, order_id: str, options: GetOrderOptions = None
    ) -> 'asyncio.Future[MetatraderOrder]':
        """Returns specific open order for a MetaTrader account.

        Args:
            account_id: Id of the MetaTrader account to return information for.
            order_id: Order id (ticket number).
            options: Additional request options.

        Returns:
            A coroutine resolving with metatrader order found.
        """
        if options is None:
            options = {}
        response = await self.rpc_request(
            account_id, {'application': 'RPC', 'type': 'getOrder', 'orderId': order_id, **options}
        )
        return response.get('order')

    async def get_history_orders_by_ticket(self, account_id: str, ticket: str) -> MetatraderHistoryOrders:
        """Returns the history of completed orders for a specific ticket number.

        Args:
            account_id: Id of the MetaTrader account to return information for.
            ticket: Ticket number (order id).

        Returns:
            A coroutine resolving with request results containing history orders found.
        """
        response = await self.rpc_request(
            account_id, {'application': 'RPC', 'type': 'getHistoryOrdersByTicket', 'ticket': ticket}
        )
        return {'historyOrders': response['historyOrders'], 'synchronizing': response['synchronizing']}

    async def get_history_orders_by_position(self, account_id: str, position_id: str) -> MetatraderHistoryOrders:
        """Returns the history of completed orders for a specific position id.

        Args:
            account_id: Id of the MetaTrader account to return information for.
            position_id: Position id.

        Returns:
            A coroutine resolving with request results containing history orders found.
        """
        response = await self.rpc_request(
            account_id, {'application': 'RPC', 'type': 'getHistoryOrdersByPosition', 'positionId': position_id}
        )
        return {'historyOrders': response['historyOrders'], 'synchronizing': response['synchronizing']}

    async def get_history_orders_by_time_range(
        self, account_id: str, start_time: datetime, end_time: datetime, offset=0, limit=1000
    ) -> MetatraderHistoryOrders:
        """Returns the history of completed orders for a specific time range.

        Args:
            account_id: Id of the MetaTrader account to return information for.
            start_time: Start of time range, inclusive.
            end_time: End of time range, exclusive.
            offset: Pagination offset, default is 0.
            limit: Pagination limit, default is 1000.

        Returns:
            A coroutine resolving with request results containing history orders found.
        """
        response = await self.rpc_request(
            account_id,
            {
                'application': 'RPC',
                'type': 'getHistoryOrdersByTimeRange',
                'startTime': format_date(start_time),
                'endTime': format_date(end_time),
                'offset': offset,
                'limit': limit,
            },
        )
        return {'historyOrders': response['historyOrders'], 'synchronizing': response['synchronizing']}

    async def get_deals_by_ticket(self, account_id: str, ticket: str) -> MetatraderDeals:
        """Returns history deals with a specific ticket number.

        Args:
            account_id: Id of the MetaTrader account to return information for.
            ticket: Ticket number (deal id for MT5 or order id for MT4).

        Returns:
            A coroutine resolving with request results containing deals found.
        """
        response = await self.rpc_request(
            account_id, {'application': 'RPC', 'type': 'getDealsByTicket', 'ticket': ticket}
        )
        return {'deals': response['deals'], 'synchronizing': response['synchronizing']}

    async def get_deals_by_position(self, account_id: str, position_id: str) -> MetatraderDeals:
        """Returns history deals for a specific position id.

        Args:
            account_id: Id of the MetaTrader account to return information for.
            position_id: Position id.

        Returns:
            A coroutine resolving with request results containing deals found.
        """
        response = await self.rpc_request(
            account_id, {'application': 'RPC', 'type': 'getDealsByPosition', 'positionId': position_id}
        )
        return {'deals': response['deals'], 'synchronizing': response['synchronizing']}

    async def get_deals_by_time_range(
        self, account_id: str, start_time: datetime, end_time: datetime, offset: int = 0, limit: int = 1000
    ) -> MetatraderDeals:
        """Returns history deals with for a specific time range.

        Args:
            account_id: Id of the MetaTrader account to return information for.
            start_time: Start of time range, inclusive.
            end_time: End of time range, exclusive.
            offset: Pagination offset, default is 0.
            limit: Pagination limit, default is 1000.

        Returns:
            A coroutine resolving with request results containing deals found.
        """
        response = await self.rpc_request(
            account_id,
            {
                'application': 'RPC',
                'type': 'getDealsByTimeRange',
                'startTime': format_date(start_time),
                'endTime': format_date(end_time),
                'offset': offset,
                'limit': limit,
            },
        )
        return {'deals': response['deals'], 'synchronizing': response['synchronizing']}

    def remove_application(self, account_id: str) -> Coroutine:
        """Clears the order and transaction history of a specified application and removes the application.

        Args:
            account_id: Id of the MetaTrader account to remove history and application for.

        Returns:
            A coroutine resolving when the history is cleared.
        """
        return self.rpc_request(account_id, {'type': 'removeApplication'})

    async def trade(
        self, account_id: str, trade: MetatraderTrade, application: str = None, reliability: str = None
    ) -> 'asyncio.Future[MetatraderTradeResponse]':
        """Execute a trade on a connected MetaTrader account.

        Args:
            account_id: Id of the MetaTrader account to execute trade for.
            trade: Trade to execute (see docs for possible trade types).
            application: Application to use.
            reliability: Account reliability.

        Returns:
            A coroutine resolving with trade result.

        Raises:
            TradeException: On trade error, check error properties for error code details.
        """
        self._format_request(trade)
        if application == 'RPC':
            response = await self.rpc_request(account_id, {'type': 'trade', 'trade': trade, 'application': application})
        else:
            response = await self.rpc_request_all_instances(
                account_id,
                {
                    'type': 'trade',
                    'trade': trade,
                    'application': application or self._application,
                    'requestId': random_id(32),
                },
                reliability,
            )
        if 'response' not in response:
            response['response'] = {}
        if 'stringCode' not in response['response']:
            response['response']['stringCode'] = response['response']['description']
        if 'numericCode' not in response['response']:
            response['response']['numericCode'] = response['response']['error']
        if response['response']['stringCode'] in [
            'ERR_NO_ERROR',
            'TRADE_RETCODE_PLACED',
            'TRADE_RETCODE_DONE',
            'TRADE_RETCODE_DONE_PARTIAL',
            'TRADE_RETCODE_NO_CHANGES',
        ]:
            return response['response']
        else:
            raise TradeException(
                response['response']['message'], response['response']['numericCode'], response['response']['stringCode']
            )

    def ensure_subscribe(self, account_id: str, instance_number: int):
        """Creates a subscription manager task to send subscription requests until cancelled.

        Args:
            account_id: Account id to subscribe.
            instance_number: Instance index number.
        """
        asyncio.create_task(self._subscription_manager.schedule_subscribe(account_id, instance_number))

    def subscribe(self, account_id: str, instance_number: int = None):
        """Subscribes to the Metatrader terminal events.

        Args:
            account_id: Id of the MetaTrader account to subscribe to.
            instance_number: Instance index number.

        Returns:
            A coroutine which resolves when subscription started.
        """
        return self._subscription_manager.subscribe(account_id, instance_number)

    async def synchronize(
        self,
        account_id: str,
        instance_number: int,
        host: str,
        synchronization_id: str,
        starting_history_order_time: datetime,
        starting_deal_time: datetime,
        hashes,
    ) -> Coroutine:
        """Requests the terminal to start synchronization process.

        Args:
            account_id: Id of the MetaTrader account to synchronize.
            instance_number: Instance index number.
            host: Name of host to synchronize with.
            synchronization_id: Synchronization request id.
            starting_history_order_time: From what date to start synchronizing history orders from. If not specified,
            the entire order history will be downloaded.
            starting_deal_time: From what date to start deal synchronization from. If not specified, then all
            history deals will be downloaded.
            hashes: terminal state hashes.

        Returns:
            A coroutine which resolves when synchronization is started.
        """
        if self._get_socket_instance_by_account(account_id, instance_number) is None:
            self._logger.debug(f'{account_id}:{instance_number}: creating socket instance on synchronize')
            await self._create_socket_instance_by_account(account_id, instance_number)
        sync_throttler = self._get_socket_instance_by_account(account_id, instance_number)['synchronizationThrottler']
        self._synchronization_hashes[synchronization_id] = hashes
        self._synchronization_hashes[synchronization_id]['lastUpdated'] = datetime.now().timestamp()
        return await sync_throttler.schedule_synchronize(
            account_id,
            {
                'requestId': synchronization_id,
                'type': 'synchronize',
                'version': 2,
                'startingHistoryOrderTime': format_date(starting_history_order_time),
                'startingDealTime': format_date(starting_deal_time),
                'instanceIndex': instance_number,
                'host': host,
            },
            hashes,
        )

    def wait_synchronized(
        self,
        account_id: str,
        instance_number: int,
        application_pattern: str,
        timeout_in_seconds: float,
        application: str = None,
    ):
        """Waits for server-side terminal state synchronization to complete.

        Args:
            account_id: Id of the MetaTrader account to synchronize.
            instance_number: Instance index number.
            application_pattern: MetaApi application regular expression pattern, default is .*
            timeout_in_seconds: Timeout in seconds, default is 300 seconds.
            application: Application to synchronize with.
        """
        return self.rpc_request(
            account_id,
            {
                'type': 'waitSynchronized',
                'applicationPattern': application_pattern,
                'timeoutInSeconds': timeout_in_seconds,
                'instanceIndex': instance_number,
                'application': application or self._application,
            },
            timeout_in_seconds + 1,
        )

    def subscribe_to_market_data(
        self, account_id: str, symbol: str, subscriptions: List[MarketDataSubscription] = None, reliability: str = None
    ) -> Coroutine:
        """Subscribes on market data of specified symbol.

        Args:
            account_id: Id of the MetaTrader account.
            symbol: Symbol (e.g. currency pair or an index).
            subscriptions: Array of market data subscription to create or update.
            reliability: Account reliability.

        Returns:
            A coroutine which resolves when subscription request was processed.
        """
        packet = {'type': 'subscribeToMarketData', 'symbol': symbol}
        if subscriptions is not None:
            packet['subscriptions'] = subscriptions
        return self.rpc_request_all_instances(account_id, packet, reliability)

    def refresh_market_data_subscriptions(
        self, account_id: str, instance_number: int, subscriptions: List[dict]
    ) -> Coroutine:
        """Refreshes market data subscriptions on the server to prevent them from expiring.

        Args:
            account_id: Id of the MetaTrader account.
            instance_number: Instance index number.
            subscriptions: Array of subscriptions to refresh.

        Returns:
            A coroutine which resolves when refresh request was processed.
        """
        return self.rpc_request(
            account_id,
            {
                'type': 'refreshMarketDataSubscriptions',
                'subscriptions': subscriptions,
                'instanceIndex': instance_number,
            },
        )

    def unsubscribe_from_market_data(
        self,
        account_id: str,
        symbol: str,
        subscriptions: List[MarketDataUnsubscription] = None,
        reliability: str = None,
    ) -> Coroutine:
        """Unsubscribes from market data of specified symbol.

        Args:
            account_id: Id of the MetaTrader account.
            symbol: Symbol (e.g. currency pair or an index).
            subscriptions: Array of subscriptions to cancel.
            reliability: Account reliability.

        Returns:
            A coroutine which resolves when unsubscription request was processed.
        """
        packet = {'type': 'unsubscribeFromMarketData', 'symbol': symbol}
        if subscriptions is not None:
            packet['subscriptions'] = subscriptions
        return self.rpc_request_all_instances(account_id, packet, reliability)

    async def get_symbols(self, account_id: str) -> 'asyncio.Future[List[str]]':
        """Retrieves symbols available on an account.

        Args:
            account_id: Id of the MetaTrader account to retrieve symbols for.

        Returns:
            A coroutine which resolves when symbols are retrieved.
        """
        response = await self.rpc_request(account_id, {'application': 'RPC', 'type': 'getSymbols'})
        return response['symbols']

    async def get_symbol_specification(
        self, account_id: str, symbol: str
    ) -> 'asyncio.Future[MetatraderSymbolSpecification]':
        """Retrieves specification for a symbol.

        Args:
            account_id: Id of the MetaTrader account to retrieve symbol specification for.
            symbol: Symbol to retrieve specification for.

        Returns:
            A coroutine which resolves when specification is retrieved.
        """
        response = await self.rpc_request(
            account_id, {'application': 'RPC', 'type': 'getSymbolSpecification', 'symbol': symbol}
        )
        return response['specification']

    async def get_symbol_price(
        self, account_id: str, symbol: str, keep_subscription: bool = False
    ) -> 'asyncio.Future[MetatraderSymbolPrice]':
        """Retrieves price for a symbol.

        Args:
            account_id: Id of the MetaTrader account to retrieve symbol price for.
            symbol: Symbol to retrieve price for.
            keep_subscription: If set to true, the account will get a long-term subscription to symbol market data.
            Long-term subscription means that on subsequent calls you will get updated value faster. If set to false or
            not set, the subscription will be set to expire in 12 minutes.

        Returns:
            A coroutine which resolves when price is retrieved.
        """
        response = await self.rpc_request(
            account_id,
            {'application': 'RPC', 'type': 'getSymbolPrice', 'symbol': symbol, 'keepSubscription': keep_subscription},
        )
        return response['price']

    async def get_candle(
        self, account_id: str, symbol: str, timeframe: str, keep_subscription: bool = False
    ) -> 'asyncio.Future[MetatraderCandle]':
        """Retrieves price for a symbol.

        Args:
            account_id: Id of the MetaTrader account to retrieve candle for.
            symbol: Symbol to retrieve candle for.
            timeframe: Defines the timeframe according to which the candle must be generated. Allowed values for
            MT5 are 1m, 2m, 3m, 4m, 5m, 6m, 10m, 12m, 15m, 20m, 30m, 1h, 2h, 3h, 4h, 6h, 8h, 12h, 1d, 1w, 1mn.
            Allowed values for MT4 are 1m, 5m, 15m 30m, 1h, 4h, 1d, 1w, 1mn.
            keep_subscription: If set to true, the account will get a long-term subscription to symbol market data.
            Long-term subscription means that on subsequent calls you will get updated value faster. If set to false or
            not set, the subscription will be set to expire in 12 minutes.

        Returns:
            A coroutine which resolves when candle is retrieved.
        """
        response = await self.rpc_request(
            account_id,
            {
                'application': 'RPC',
                'type': 'getCandle',
                'symbol': symbol,
                'timeframe': timeframe,
                'keepSubscription': keep_subscription,
            },
        )
        return response['candle']

    async def get_tick(
        self, account_id: str, symbol: str, keep_subscription: bool = False
    ) -> 'asyncio.Future[MetatraderTick]':
        """Retrieves latest tick for a symbol. MT4 G1 accounts do not support this API.

        Args:
            account_id: Id of the MetaTrader account to retrieve symbol tick for.
            symbol: Symbol to retrieve tick for.
            keep_subscription: If set to true, the account will get a long-term subscription to symbol market data.
            Long-term subscription means that on subsequent calls you will get updated value faster. If set to false or
            not set, the subscription will be set to expire in 12 minutes.

        Returns:
            A coroutine which resolves when tick is retrieved.
        """
        response = await self.rpc_request(
            account_id,
            {'application': 'RPC', 'type': 'getTick', 'symbol': symbol, 'keepSubscription': keep_subscription},
        )
        return response['tick']

    async def get_book(
        self, account_id: str, symbol: str, keep_subscription: bool = False
    ) -> 'asyncio.Future[MetatraderBook]':
        """Retrieves latest order book for a symbol. MT4 accounts do not support this API.

        Args:
            account_id: Id of the MetaTrader account to retrieve symbol order book for.
            symbol: Symbol to retrieve order book for.
            keep_subscription: If set to true, the account will get a long-term subscription to symbol market data.
            Long-term subscription means that on subsequent calls you will get updated value faster. If set to false or
            not set, the subscription will be set to expire in 12 minutes.

        Returns:
            A coroutine which resolves when order book is retrieved.
        """
        response = await self.rpc_request(
            account_id,
            {'application': 'RPC', 'type': 'getBook', 'symbol': symbol, 'keepSubscription': keep_subscription},
        )
        return response['book']

    async def refresh_terminal_state(self, account_id: str) -> List[str]:
        """Forces refresh of most recent quote updates for symbols subscribed to by the terminal.

        Args:
            account_id: Id of the MetaTrader account.

        Returns:
            A coroutine which resolves with recent quote symbols that was initiated to process.
        """
        response = await self.rpc_request(account_id, {'application': 'RPC', 'type': 'refreshTerminalState'})
        return response['symbols']

    async def refresh_symbol_quotes(self, account_id: str, symbols: List[str]) -> RefreshedQuotes:
        """Forces refresh and retrieves latest quotes for a subset of symbols the terminal is subscribed to.

        Args:
            account_id: Id of the MetaTrader account.
            symbols: Quote symbols to refresh.

        Returns:
            Refreshed quotes and basic account information info.
        """
        response = await self.rpc_request(
            account_id, {'application': 'RPC', 'type': 'refreshSymbolQuotes', 'symbols': symbols}
        )
        return response['refreshedQuotes']

    def save_uptime(self, account_id: str, uptime: Dict):
        """Sends client uptime stats to the server.

        Args:
            account_id: Id of the MetaTrader account to save uptime.
            uptime: Uptime statistics to send to the server.

        Returns:
            A coroutine which resolves when uptime statistics is submitted.
        """
        return self.rpc_request(account_id, {'type': 'saveUptime', 'uptime': uptime})

    async def unsubscribe(self, account_id: str):
        """Unsubscribe from account.

        Args:
            account_id: Id of the MetaTrader account to unsubscribe.

        Returns:
            A coroutine which resolves when socket is unsubscribed."""
        region = self.get_account_region(account_id)

        self._latency_service.on_unsubscribe(account_id)

        for key in self._update_events.copy():
            if key.startswith(account_id):
                del self._update_events[key]

        async def unsubscribe_job(instance_number):
            try:
                await self._subscription_manager.unsubscribe(account_id, int(instance_number))
                if (
                    instance_number in self._socket_instances_by_accounts
                    and account_id in self._socket_instances_by_accounts[instance_number]
                ):
                    del self._socket_instances_by_accounts[instance_number][account_id]
            except (NotFoundException, TimeoutException):
                pass
            except Exception as err:
                self._logger.warn(f'{account_id}:{instance_number}: failed to unsubscribe', string_format_error(err))

        await asyncio.gather(
            *list(
                map(
                    lambda instance_number: asyncio.create_task(unsubscribe_job(instance_number)),
                    self._socket_instances[region].keys(),
                )
            )
        )

    async def get_server_time(self, account_id: str) -> ServerTime:
        """Returns server time for a specified MetaTrader account.

        Args:
            account_id: Id of the MetaTrader account to return server time for.

        Returns:
            A coroutine resolving with server time.
        """
        response = await self.rpc_request(account_id, {'application': 'RPC', 'type': 'getServerTime'})
        return response['serverTime']

    async def calculate_margin(self, account_id: str, application: str, reliability: str, order: MarginOrder) -> Margin:
        """Calculates margin required to open a trade on the specified trading account.

        Args:
            account_id: Id of the trading account to calculate margin for.
            application: Application to send the request to.
            reliability: Account reliability.
            order: Order to calculate margin for.

        Returns:
            A coroutine resolving with margin calculation result.
        """
        if application == 'RPC':
            response = await self.rpc_request(
                account_id, {'application': application, 'type': 'calculateMargin', 'order': order}
            )
        else:
            response = await self.rpc_request_all_instances(
                account_id, {'application': application, 'type': 'calculateMargin', 'order': order}, reliability
            )
        return response['margin']

    async def unsubscribe_account_region(self, account_id: str, region: str):
        """Calls on_unsubscribe_region listener event.

        Args:
            account_id: Account id.
            region: Account region to unsubscribe.
        """
        unsubscribe_tasks: List[asyncio.Task] = []

        if account_id in self._synchronization_listeners:
            for listener in self._synchronization_listeners[account_id]:

                async def run_on_unsubscribe_region(listener):
                    try:
                        await self._process_event(
                            lambda: listener.on_unsubscribe_region(region),
                            f'{account_id}:{region}:on_unsubscribe_region',
                            True,
                        )
                    except Exception as err:
                        self._logger.error(
                            f'{account_id}:{region}: Failed to notify listener '
                            f'about on_unsubscribe_region event ' + string_format_error(err)
                        )

                unsubscribe_tasks.append(run_on_unsubscribe_region(listener))
        if len(unsubscribe_tasks) > 0:
            await asyncio.gather(*unsubscribe_tasks)

    def add_synchronization_listener(self, account_id: str, listener: SynchronizationListener):
        """Adds synchronization listener for specific account.

        Args:
            account_id: Account id.
            listener: Synchronization listener to add.
        """
        self._logger.debug(f'{account_id}: Added synchronization listener')
        if account_id in self._synchronization_listeners:
            listeners = self._synchronization_listeners[account_id]
        else:
            listeners = []
            self._synchronization_listeners[account_id] = listeners
        listeners.append(listener)

    def remove_synchronization_listener(self, account_id: str, listener: SynchronizationListener):
        """Removes synchronization listener for specific account.

        Args:
            account_id: Account id.
            listener: Synchronization listener to remove.
        """
        self._logger.debug(f'{account_id}: Removed synchronization listener')
        listeners = self._synchronization_listeners[account_id]

        if not listeners:
            listeners = []
        elif listeners.__contains__(listener):
            listeners.remove(listener)
        self._synchronization_listeners[account_id] = listeners

    def add_latency_listener(self, listener: LatencyListener):
        """Adds latency listener.

        Args:
            listener: Latency listener to add."""
        self._latency_listeners.append(listener)

    def remove_latency_listener(self, listener: LatencyListener):
        """Removes latency listener.

        Args:
            listener: Latency listener to remove."""
        self._latency_listeners = list(filter(lambda lis: lis != listener, self._latency_listeners))

    def add_reconnect_listener(self, listener: ReconnectListener, account_id: str):
        """Adds reconnect listener.

        Args:
            listener: Reconnect listener to add.
            account_id: Account id of listener.
        """

        self._reconnect_listeners.append({'accountId': account_id, 'listener': listener})

    def remove_reconnect_listener(self, listener: ReconnectListener):
        """Removes reconnect listener.

        Args:
            listener: Listener to remove.
        """
        for i in range(len(self._reconnect_listeners)):
            if self._reconnect_listeners[i]['listener'] == listener:
                self._reconnect_listeners.remove(self._reconnect_listeners[i])
                break

    def remove_all_listeners(self):
        """Removes all listeners. Intended for use in unit tests."""

        self._synchronization_listeners = {}
        self._reconnect_listeners = []

    def on_account_deleted(self, account_id: str):
        """Clears account or replica data from client records and unsubscribes.

        Args:
            account_id: Account id to process the removal of.
        """
        self._subscription_manager.cancel_account(account_id)
        self._latency_service.on_unsubscribe(account_id)
        master_account_id = self._accounts_by_replica_id.get(account_id)

        if master_account_id:
            if master_account_id == account_id:
                region_data = self._account_replicas[master_account_id]

                for instance in self._synchronization_id_by_instance.keys():
                    if instance.startswith(master_account_id):
                        del self._synchronization_id_by_instance[instance]

                replicas = region_data.values()

                for replica in replicas:
                    for instance in self._socket_instances_by_accounts.values():
                        if replica in instance:
                            del instance[replica]

                    del self._accounts_by_replica_id[replica]
                    del self._regions_by_accounts[replica]

                del self._account_replicas[master_account_id]
                self._logger.debug(f"{master_account_id}: processed primary account removal")
            else:
                for instance in self._socket_instances_by_accounts.values():
                    if account_id in instance:
                        del instance[account_id]

                region_data = self._regions_by_accounts.get(account_id)

                if region_data:
                    region = region_data['region']

                    for instance in list(self._synchronization_id_by_instance.keys()):
                        if instance.startswith(f"{master_account_id}:{region}"):
                            del self._synchronization_id_by_instance[instance]

                    del self._account_replicas[master_account_id][region]
                    self._logger.debug(f"{master_account_id}: processed removal of replica ${account_id}")
                del self._accounts_by_replica_id[account_id]
                del self._regions_by_accounts[account_id]

    def queue_packet(self, packet: dict):
        """Queues an account packet for processing.

        Args:
            packet: Packet to process.
        """
        account_id = packet['accountId']
        packets = self._packet_orderer.restore_order(packet)
        packets = list(filter(lambda e: e['type'] != 'noop', packets))
        if self._sequential_event_processing and 'sequenceNumber' in packet:
            events = list(map(lambda packet: lambda: self._process_synchronization_packet(packet), packets))
            if account_id not in self._event_queues:
                self._event_queues[account_id] = events
                asyncio.create_task(self._call_account_events(account_id))
            else:
                self._event_queues[account_id] += events
        else:
            for packet in packets:
                asyncio.create_task(self._process_synchronization_packet(packet))

    def queue_event(self, account_id: str, name: str, callable_: Callable):
        """Queues an account event for processing.

        Args:
            account_id: Account id.
            name: Event label name.
            callable_: Async function to execute.
        """

        async def event():
            return await self._process_event(callable_, f'{account_id}:{name}')

        if self._sequential_event_processing:
            if account_id not in self._event_queues:
                self._event_queues[account_id] = [event]
                asyncio.create_task(self._call_account_events(account_id))
            else:
                self._event_queues[account_id].append(event)
        else:
            asyncio.create_task(event())

    async def _call_account_events(self, account_id):
        if account_id in self._event_queues:
            for event in self._event_queues[account_id]:
                await event()
            del self._event_queues[account_id]

    async def _reconnect(self, instance_number: int, socket_instance_index: int, region: str):
        reconnected = False
        instance = self._socket_instances[region][instance_number][socket_instance_index]
        if not instance['isReconnecting']:
            instance['isReconnecting'] = True
            while instance['connected'] and not reconnected:
                instance['reconnectWaitTime'] = min(instance['reconnectWaitTime'] * 2, 30)
                await asyncio.sleep(instance['reconnectWaitTime'])
                try:
                    await instance['socket'].disconnect()
                    client_id = "{:01.10f}".format(random())
                    instance['connectResult'] = asyncio.Future()
                    instance['resolved'] = False
                    instance['sessionId'] = random_id()
                    server_url = await self._get_server_url(instance_number, socket_instance_index, region)
                    url = f'{server_url}?auth-token={self._token}&clientId={client_id}&protocol=3'
                    await asyncio.wait_for(
                        instance['socket'].connect(url, socketio_path='ws', headers={'Client-Id': client_id}),
                        timeout=self._connect_timeout,
                    )
                    await asyncio.wait_for(instance['connectResult'], self._connect_timeout)
                    reconnected = True
                    instance['isReconnecting'] = False
                    await self._fire_reconnected(instance_number, socket_instance_index, region)
                    await instance['socket'].wait()
                except Exception as err:
                    instance['connectResult'].cancel()
                    instance['connectResult'] = None

    async def rpc_request_all_instances(
        self, account_id: str, request: dict, reliability=None, timeout_in_seconds=None
    ):
        """Simultaneously sends RPC requests to all synchronized instances.

        Args:
            account_id: Metatrader account id.
            request: Base request data
            reliability: Account reliability.
            timeout_in_seconds: Request timeout in seconds.
        """
        if reliability == 'high':

            async def generate_rpc_request(instance_number):
                req = copy(request)
                req['instanceIndex'] = instance_number
                return await self.rpc_request(account_id, req, timeout_in_seconds)

            return await promise_any(
                [asyncio.create_task(generate_rpc_request(0)), asyncio.create_task(generate_rpc_request(1))]
            )
        else:
            return await self.rpc_request(account_id, request, timeout_in_seconds)

    async def rpc_request(self, account_id: str, request: dict, timeout_in_seconds: float = None) -> Coroutine:
        """Makes a RPC request.

        Args:
            account_id: Metatrader account id.
            request: Base request data.
            timeout_in_seconds: Request timeout in seconds.
        """
        ignored_request_types = ['subscribe', 'synchronize', 'refreshMarketDataSubscriptions', 'unsubscribe']
        primary_account_id = self._accounts_by_replica_id[account_id]
        connected_instances = self._latency_service.get_active_account_instances(primary_account_id)
        connected_instance = connected_instances[0] if len(connected_instances) else None
        if request['type'] not in ignored_request_types:
            if not connected_instance:
                connected_instance = await self._latency_service.wait_connected_instance(account_id)
            active_region = connected_instance.split(':')[1]
            account_id = self._account_replicas[primary_account_id][active_region]

        instance_number = 0
        region = self.get_account_region(account_id)
        self._refresh_account_region(account_id)
        if 'instanceIndex' in request and request['instanceIndex'] is not None:
            instance_number = request['instanceIndex']
        else:
            if connected_instance:
                instance_number = int(connected_instance.split(':')[2])

            if 'application' not in request or request['application'] != 'RPC':
                request = copy(request)
                request['instanceIndex'] = instance_number

        if instance_number not in self._socket_instances_by_accounts:
            self._socket_instances_by_accounts[instance_number] = {}

        if region not in self._socket_instances:
            self._socket_instances[region] = {}

        if instance_number not in self._socket_instances[region]:
            self._socket_instances[region][instance_number] = []

        if account_id in self._socket_instances_by_accounts[instance_number]:
            socket_instance_index = self._socket_instances_by_accounts[instance_number][account_id]
        else:
            self._logger.debug(f'{account_id}:{instance_number}: creating socket instance on RPC request')
            await self._create_socket_instance_by_account(account_id, instance_number)
            socket_instance_index = self._socket_instances_by_accounts[instance_number][account_id]
        instance = self._socket_instances[region][instance_number][socket_instance_index]
        start_time = datetime.now()
        while not instance['resolved'] and (start_time + timedelta(seconds=self._connect_timeout) > datetime.now()):
            await asyncio.sleep(1)
        if not instance['resolved']:
            raise TimeoutException(
                f"MetaApi websocket client request of account {account_id} timed out because "
                "socket client failed to connect to the server."
            )
        if request['type'] == 'subscribe':
            request['sessionId'] = instance['sessionId']
        if request['type'] in ['trade', 'subscribe']:
            return await self._make_request(account_id, instance_number, request, timeout_in_seconds)
        retry_counter = 0
        while True:
            try:
                return await self._make_request(account_id, instance_number, request, timeout_in_seconds)
            except TooManyRequestsException as err:
                calc_retry_counter = retry_counter
                calc_request_time = 0
                while calc_retry_counter < self._retries:
                    calc_retry_counter += 1
                    calc_request_time += min(
                        pow(2, calc_retry_counter) * self._min_retry_delay_in_seconds, self._max_retry_delay_in_seconds
                    )

                retry_time = date(err.metadata['recommendedRetryTime']).timestamp()
                if (datetime.now().timestamp() + calc_request_time) > retry_time and retry_counter < self._retries:
                    if datetime.now().timestamp() < retry_time:
                        await asyncio.sleep(retry_time - datetime.now().timestamp())
                    retry_counter += 1
                else:
                    raise err
                if account_id not in self._socket_instances_by_accounts[instance_number]:
                    raise err
            except Exception as err:
                if (
                    err.__class__.__name__
                    in [
                        'NotSynchronizedException',
                        'TimeoutException',
                        'NotAuthenticatedException',
                        'InternalException',
                    ]
                    and retry_counter < self._retries
                ):
                    await asyncio.sleep(
                        min(pow(2, retry_counter) * self._min_retry_delay_in_seconds, self._max_retry_delay_in_seconds)
                    )
                    retry_counter += 1
                else:
                    raise err
                if (
                    instance_number not in self._socket_instances_by_accounts
                    or account_id not in self._socket_instances_by_accounts[instance_number]
                ):
                    raise err

    async def _make_request(
        self, account_id: str, instance_number: int, request: dict, timeout_in_seconds: float = None
    ):
        socket_instance = self._get_socket_instance_by_account(account_id, instance_number)
        if 'requestId' in request:
            request_id = request['requestId']
        else:
            request_id = random_id()
            request['requestId'] = request_id
        request['timestamps'] = {'clientProcessingStarted': format_date(datetime.now())}
        socket_instance['requestResolves'][request_id] = asyncio.Future()
        socket_instance['requestResolves'][request_id].type = request['type']
        request['accountId'] = account_id
        request['application'] = request.get('application', self._application)

        resolve = None

        if (
            request['type'] == 'unsubscribe'
            or request['application'] == 'RPC'
            or ('instanceIndex' in request and request['instanceIndex'] == socket_instance['instanceNumber'])
        ):
            self._logger.debug(f'{account_id}: Sending request: {json.dumps(request)}')
            await socket_instance['socket'].emit('request', request)
            try:
                resolve = await asyncio.wait_for(
                    socket_instance['requestResolves'][request_id], timeout=timeout_in_seconds or self._request_timeout
                )
            except asyncio.TimeoutError:
                if request_id in socket_instance['requestResolves']:
                    del socket_instance['requestResolves'][request_id]
                raise TimeoutException(
                    f"MetaApi websocket client request {request['requestId']} of type "
                    f"{request['type']} timed out. Please make sure your account is connected "
                    f"to broker before retrying your request."
                )
        else:
            self._logger.debug(
                f'{account_id}:{request["instanceIndex"]}: skipping request because it is being sent '
                + f'to the socket of the wrong instance index, request={json.dumps(request)}'
            )
        return resolve

    def _convert_error(self, data) -> Exception:
        if data['error'] == 'ValidationError':
            return ValidationException(data['message'], data.get('details'))
        elif data['error'] == 'NotFoundError':
            return NotFoundException(data['message'])
        elif data['error'] == 'NotSynchronizedError':
            return NotSynchronizedException(data['message'])
        elif data['error'] == 'TimeoutError':
            return TimeoutException(data['message'])
        elif data['error'] == 'NotAuthenticatedError':
            return NotConnectedException(data['message'])
        elif data['error'] == 'ForbiddenError':
            return ForbiddenException(data['message'])
        elif data['error'] == 'TradeError':
            return TradeException(data['message'], data['numericCode'], data['stringCode'])
        elif data['error'] == 'UnauthorizedError':
            self.close()
            return UnauthorizedException(data['message'])
        elif data['error'] == 'TooManyRequestsError':
            return TooManyRequestsException(data['message'], data['metadata'])
        else:
            return InternalException(data['message'])

    def _format_request(self, packet: dict or list):
        if not isinstance(packet, str):
            for field in packet:
                value = packet[field]
                if isinstance(value, datetime):
                    packet[field] = format_date(value)
                elif isinstance(value, list):
                    for item in value:
                        self._format_request(item)
                elif isinstance(value, dict):
                    self._format_request(value)

    def _convert_iso_time_to_date(self, packet, field_name=''):
        try:
            if not (isinstance(packet, str) or isinstance(packet, int)):
                for field in packet:
                    value = packet[field]
                    if (
                        isinstance(value, str)
                        and re.search('time|Time', field)
                        and not re.search('brokerTime|BrokerTime|timeframe', field)
                    ):
                        packet[field] = date(value)
                    if isinstance(value, list):
                        for item in value:
                            self._convert_iso_time_to_date(item, field)
                    if isinstance(value, dict):
                        self._convert_iso_time_to_date(value, field)
                if packet and 'timestamps' in packet:
                    for field in packet['timestamps']:
                        packet['timestamps'][field] = date(packet['timestamps'][field])
                if packet and 'type' in packet and packet['type'] == 'prices':
                    if 'prices' in packet:
                        for price in packet['prices']:
                            if 'timestamps' in price:
                                for field in price['timestamps']:
                                    if isinstance(price['timestamps'][field], str):
                                        price['timestamps'][field] = date(price['timestamps'][field])
        except Exception as err:
            self._logger.error(f'Failed to convert date for field {field_name}:', err)

    async def _process_synchronization_packet(self, data):
        try:
            instance_number = data.get('instanceIndex', 0)
            socket_instance = self._get_socket_instance_by_account(data['accountId'], instance_number)
            if 'synchronizationId' in data and socket_instance:
                socket_instance['synchronizationThrottler'].update_synchronization_id(data['synchronizationId'])
            region = self.get_account_region(data['accountId'])
            primary_account_id = (
                self._accounts_by_replica_id[data['accountId']]
                if data['accountId'] in self._accounts_by_replica_id
                else data['accountId']
            )
            instance_id = primary_account_id + ':' + region + ':' + str(instance_number) + ':' + data.get('host', '0')
            instance_index = region + ':' + str(instance_number) + ':' + data.get('host', '0')

            def is_only_active_instance():
                active_instance_ids = list(
                    filter(
                        lambda instance: instance.startswith(
                            primary_account_id + ':' + region + ':' + str(instance_number)
                        ),
                        self._connected_hosts.keys(),
                    )
                )
                return not active_instance_ids or (
                    len(active_instance_ids) == 1 and active_instance_ids[0] == instance_id
                )

            def cancel_disconnect_timer():
                if instance_id in self._status_timers:
                    self._status_timers[instance_id].cancel()

            def reset_disconnect_timer():
                async def disconnect():
                    await asyncio.sleep(60)
                    self._logger.debug(
                        f"{data.get('accountId')}:{instance_index}: timed out waiting for connection status"
                    )
                    if is_only_active_instance():
                        self._subscription_manager.on_timeout(data["accountId"], 0)
                        self._subscription_manager.on_timeout(data["accountId"], 1)
                    self.queue_event(
                        primary_account_id, f'{instance_index}:onDisconnected', lambda: on_disconnected(True)
                    )

                cancel_disconnect_timer()
                self._status_timers[instance_id] = asyncio.create_task(disconnect())

            async def on_disconnected(is_timeout: bool = False):
                if instance_id in self._connected_hosts:
                    self._latency_service.on_disconnected(instance_id)
                    if is_only_active_instance():
                        if primary_account_id in self._synchronization_listeners:
                            for listener in self._synchronization_listeners[primary_account_id]:

                                def run_on_disconnected(listener: SynchronizationListener):
                                    return lambda: listener.on_disconnected(instance_index)

                                await self._process_event(
                                    run_on_disconnected(listener),
                                    f'{primary_account_id}:{instance_index}:on_disconnected',
                                )

                    self._packet_orderer.on_stream_closed(instance_id)
                    if socket_instance:
                        socket_instance['synchronizationThrottler'].remove_id_by_parameters(
                            data['accountId'], instance_number, data.get('host')
                        )

                    if primary_account_id in self._synchronization_listeners:
                        for listener in self._synchronization_listeners[primary_account_id]:

                            def run_on_stream_closed(listener: SynchronizationListener):
                                return lambda: listener.on_stream_closed(instance_index)

                            await self._process_event(
                                run_on_stream_closed(listener),
                                f'{primary_account_id}:{instance_index}:on_stream_closed',
                            )
                    if instance_id in self._connected_hosts:
                        del self._connected_hosts[instance_id]

                    if is_only_active_instance() and not is_timeout:
                        await self._subscription_manager.on_disconnected(data['accountId'], 0)
                        await self._subscription_manager.on_disconnected(data['accountId'], 1)

            if data['type'] == 'authenticated':
                reset_disconnect_timer()
                if 'sessionId' not in data or socket_instance and data['sessionId'] == socket_instance['sessionId']:
                    asyncio.create_task(self._latency_service.on_connected(instance_id))
                    if 'host' in data:
                        self._connected_hosts[instance_id] = data['host']

                    if primary_account_id in self._synchronization_listeners:
                        for listener in self._synchronization_listeners[primary_account_id]:

                            def run_on_connected(listener):
                                return lambda: listener.on_connected(instance_index, data['replicas'])

                            await self._process_event(
                                run_on_connected(listener), f'{primary_account_id}:{instance_index}:on_connected'
                            )
                        self._subscription_manager.cancel_subscribe(data['accountId'] + ':' + str(instance_number))
                    if data['replicas'] == 1:
                        self._subscription_manager.cancel_account(data['accountId'])
                    else:
                        self._subscription_manager.cancel_subscribe(data['accountId'] + ':' + str(instance_number))

            elif data['type'] == 'disconnected':
                cancel_disconnect_timer()
                await on_disconnected()
            elif data['type'] == 'synchronizationStarted':
                self._update_events[instance_id] = []
                self._synchronization_flags[data['synchronizationId']] = {
                    'accountId': data['accountId'],
                    'instanceNumber': instance_number,
                    'specificationsUpdated': data.get('specificationsHashIndex') is None,
                    'positionsUpdated': data.get('positionsHashIndex') is None,
                    'ordersUpdated': data.get('ordersHashIndex') is None,
                }
                self._synchronization_id_by_instance[instance_id] = data['synchronizationId']
                specifications_hash = (
                    self._synchronization_hashes.get(data['synchronizationId'])
                    and self._synchronization_hashes[data['synchronizationId']]['specificationsHashes'][
                        data['specificationsHashIndex']
                    ]
                    if data.get('specificationsHashIndex') is not None
                    else None
                )
                positions_hash = (
                    self._synchronization_hashes.get(data['synchronizationId'])
                    and self._synchronization_hashes[data['synchronizationId']]['positionsHashes'][
                        data['positionsHashIndex']
                    ]
                    if data.get('positionsHashIndex') is not None
                    else None
                )
                orders_hash = (
                    self._synchronization_hashes.get(data['synchronizationId'])
                    and self._synchronization_hashes[data['synchronizationId']]['ordersHashes'][data['ordersHashIndex']]
                    if data.get('ordersHashIndex') is not None
                    else None
                )
                if data.get('synchronizationId') in self._synchronization_hashes:
                    del self._synchronization_hashes[data['synchronizationId']]

                for listener in self._synchronization_listeners.get(primary_account_id, []):
                    await self._process_event(
                        lambda: listener.on_synchronization_started(
                            instance_index,
                            specifications_hash=specifications_hash,
                            positions_hash=positions_hash,
                            orders_hash=orders_hash,
                            synchronization_id=data['synchronizationId'],
                        ),
                        f'{primary_account_id}:{instance_index}:on_synchronization_started',
                    )

            elif data['type'] == 'accountInformation':
                if 'synchronizationId' in data and (
                    instance_id not in self._synchronization_id_by_instance
                    or data['synchronizationId'] != self._synchronization_id_by_instance[instance_id]
                ):
                    return
                if data['accountInformation'] and (primary_account_id in self._synchronization_listeners):

                    async def run_on_account_info(listener: SynchronizationListener):
                        try:
                            await self._process_event(
                                lambda: listener.on_account_information_updated(
                                    instance_index, data['accountInformation']
                                ),
                                f'{primary_account_id}:{instance_index}:on_account_information_updated',
                                True,
                            )
                            if (
                                'synchronizationId' in data
                                and data['synchronizationId'] in self._synchronization_flags
                                and not self._synchronization_flags[data['synchronizationId']]['positionsUpdated']
                            ):
                                await self._process_event(
                                    lambda: listener.on_positions_synchronized(
                                        instance_index, data['synchronizationId']
                                    ),
                                    f'{primary_account_id}:{instance_index}:on_positions_synchronized',
                                    True,
                                )
                                if not self._synchronization_flags[data['synchronizationId']]['ordersUpdated']:
                                    await self._process_event(
                                        lambda: listener.on_pending_orders_synchronized(
                                            instance_index, data['synchronizationId']
                                        ),
                                        f'{primary_account_id}:{instance_index}:on_pending_orders_synchronized',
                                        True,
                                    )
                        except Exception as err:
                            self._logger.error(
                                f'{primary_account_id}:{instance_index}: Failed to notify listener '
                                f'about accountInformation event ' + string_format_error(err)
                            )

                    for listener in self._synchronization_listeners[primary_account_id]:
                        await run_on_account_info(listener)

                    if (
                        'synchronizationId' in data
                        and data['synchronizationId'] in self._synchronization_flags
                        and not self._synchronization_flags[data['synchronizationId']]['positionsUpdated']
                        and not self._synchronization_flags[data['synchronizationId']]['ordersUpdated']
                    ):
                        del self._synchronization_flags[data['synchronizationId']]
            elif data['type'] == 'deals':
                if 'synchronizationId' in data and (
                    data['synchronizationId'] != self._synchronization_id_by_instance.get(instance_id)
                ):
                    return
                if 'deals' in data:
                    for deal in data['deals']:
                        if primary_account_id in self._synchronization_listeners:
                            for listener in self._synchronization_listeners[primary_account_id]:
                                await self._process_event(
                                    lambda: listener.on_deal_added(instance_index, deal),
                                    f'{primary_account_id}:{instance_index}:on_deal_added',
                                )

            elif data['type'] == 'orders':
                if 'synchronizationId' in data and (
                    instance_id not in self._synchronization_id_by_instance
                    or data['synchronizationId'] != self._synchronization_id_by_instance[instance_id]
                ):
                    return

                async def run_on_pending_orders_replaced(listener: SynchronizationListener):
                    try:
                        if 'orders' in data:
                            await self._process_event(
                                lambda: listener.on_pending_orders_replaced(instance_index, data['orders']),
                                f'{primary_account_id}:{instance_index}:on_pending_orders_replaced',
                                True,
                            )
                        await self._process_event(
                            lambda: listener.on_pending_orders_synchronized(instance_index, data['synchronizationId']),
                            f'{primary_account_id}:{instance_index}:on_pending_orders_synchronized',
                            True,
                        )
                    except Exception as err:
                        self._logger.error(
                            f'{primary_account_id}:{instance_index}: Failed to notify listener about '
                            f'orders event ' + string_format_error(err)
                        )

                if primary_account_id in self._synchronization_listeners:
                    for listener in self._synchronization_listeners[primary_account_id]:
                        await run_on_pending_orders_replaced(listener)

                if data['synchronizationId'] in self._synchronization_flags:
                    del self._synchronization_flags[data['synchronizationId']]
            elif data['type'] == 'historyOrders':
                if 'synchronizationId' in data and (
                    instance_id not in self._synchronization_id_by_instance
                    or data['synchronizationId'] != self._synchronization_id_by_instance[instance_id]
                ):
                    return
                if 'historyOrders' in data:
                    for history_order in data['historyOrders']:
                        if primary_account_id in self._synchronization_listeners:
                            for listener in self._synchronization_listeners[primary_account_id]:

                                def run_on_order_added(listener: SynchronizationListener):
                                    return lambda: listener.on_history_order_added(instance_index, history_order)

                                await self._process_event(
                                    run_on_order_added(listener),
                                    f'{primary_account_id}:{instance_index}:on_history_order_added',
                                )

            elif data['type'] == 'positions':
                if 'synchronizationId' in data and (
                    instance_id not in self._synchronization_id_by_instance
                    or data['synchronizationId'] != self._synchronization_id_by_instance[instance_id]
                ):
                    return

                async def run_on_positions_replaced(listener: SynchronizationListener):
                    try:
                        if 'positions' in data:
                            await self._process_event(
                                lambda: listener.on_positions_replaced(instance_index, data['positions']),
                                f'{primary_account_id}:{instance_index}:on_positions_replaced',
                                True,
                            )
                        await self._process_event(
                            lambda: listener.on_positions_synchronized(instance_index, data['synchronizationId']),
                            f'{primary_account_id}:{instance_index}:on_positions_synchronized',
                            True,
                        )
                        if (
                            data['synchronizationId'] in self._synchronization_flags
                            and not self._synchronization_flags[data['synchronizationId']]['ordersUpdated']
                        ):
                            await self._process_event(
                                lambda: listener.on_pending_orders_synchronized(
                                    instance_index, data['synchronizationId']
                                ),
                                f'{primary_account_id}:{instance_index}:on_pending_orders_synchronized',
                                True,
                            )
                    except Exception as err:
                        self._logger.error(
                            f'{primary_account_id}:{instance_index}: Failed to notify listener about '
                            f'positions event ' + string_format_error(err)
                        )

                if primary_account_id in self._synchronization_listeners:
                    for listener in self._synchronization_listeners[primary_account_id]:
                        await run_on_positions_replaced(listener)
                if (
                    data['synchronizationId'] in self._synchronization_flags
                    and not self._synchronization_flags[data['synchronizationId']]['ordersUpdated']
                ):
                    del self._synchronization_flags[data['synchronizationId']]
            elif data['type'] == 'update':
                if instance_id in self._update_events:
                    self._update_events[instance_id].append(data)
                if 'accountInformation' in data and primary_account_id in self._synchronization_listeners:
                    for listener in self._synchronization_listeners[primary_account_id]:
                        await self._process_event(
                            lambda: listener.on_account_information_updated(instance_index, data['accountInformation']),
                            f'{primary_account_id}:{instance_index}:' 'on_account_information_updated',
                        )

                updated_positions = data.get('updatedPositions', [])
                removed_position_ids = data.get('removedPositionIds', [])

                for listener in self._synchronization_listeners.get(primary_account_id, []):
                    await self._process_event(
                        lambda: listener.on_positions_updated(instance_index, updated_positions, removed_position_ids),
                        f'{primary_account_id}:{instance_index}:on_positions_updated',
                    )

                    for position in updated_positions:
                        await self._process_event(
                            lambda: listener.on_position_updated(instance_index, position),
                            f'{primary_account_id}:{instance_index}:on_position_updated',
                        )

                    for position_id in removed_position_ids:
                        await self._process_event(
                            lambda: listener.on_position_removed(instance_index, position_id),
                            f'{primary_account_id}:{instance_index}:on_position_removed',
                        )

                updated_orders = data.get('updatedOrders') or []
                completed_order_ids = data.get('completedOrderIds') or []
                if len(updated_orders) or len(completed_order_ids):
                    if primary_account_id in self._synchronization_listeners:
                        for listener in self._synchronization_listeners[primary_account_id]:

                            def run_on_pending_orders_updated(listener: SynchronizationListener):
                                return lambda: listener.on_pending_orders_updated(
                                    instance_index, updated_orders, completed_order_ids
                                )

                            await self._process_event(
                                run_on_pending_orders_updated(listener),
                                f'{primary_account_id}:{instance_index}:on_pending_orders_updated',
                            )

                for order in updated_orders:
                    if primary_account_id in self._synchronization_listeners:
                        for listener in self._synchronization_listeners[primary_account_id]:

                            def run_on_pending_order_updated(listener: SynchronizationListener):
                                return lambda: listener.on_pending_order_updated(instance_index, order)

                            await self._process_event(
                                run_on_pending_order_updated(listener),
                                f'{primary_account_id}:{instance_index}:on_pending_order_updated',
                            )

                for order_id in completed_order_ids:
                    if primary_account_id in self._synchronization_listeners:
                        for listener in self._synchronization_listeners[primary_account_id]:

                            def run_on_pending_order_completed(listener: SynchronizationListener):
                                return lambda: listener.on_pending_order_completed(instance_index, order_id)

                            await self._process_event(
                                run_on_pending_order_completed(listener),
                                f'{primary_account_id}:{instance_index}:on_pending_order_completed',
                            )
                if 'historyOrders' in data:
                    for history_order in data['historyOrders']:
                        if primary_account_id in self._synchronization_listeners:
                            for listener in self._synchronization_listeners[primary_account_id]:

                                def run_on_history_order_added(listener: SynchronizationListener):
                                    return lambda: listener.on_history_order_added(instance_index, history_order)

                                await self._process_event(
                                    run_on_history_order_added(listener),
                                    f'{primary_account_id}:{instance_index}:on_history_order_added',
                                )
                if 'deals' in data:
                    for deal in data['deals']:
                        if primary_account_id in self._synchronization_listeners:
                            for listener in self._synchronization_listeners[primary_account_id]:

                                def run_on_deal_added(listener: SynchronizationListener):
                                    return lambda: listener.on_deal_added(instance_index, deal)

                                await self._process_event(
                                    run_on_deal_added(listener), f'{primary_account_id}:{instance_index}:on_deal_added'
                                )
                if 'timestamps' in data:
                    data['timestamps']['clientProcessingFinished'] = datetime.now()
                    for listener in self._latency_listeners:

                        def run_on_update(listener: LatencyListener):
                            return lambda: listener.on_update(data['accountId'], data['timestamps'])

                        await self._process_event(
                            run_on_update(listener), f'{primary_account_id}:{instance_index}:on_update'
                        )

            elif data['type'] == 'dealSynchronizationFinished':
                if (
                    'synchronizationId' in data
                    and data['synchronizationId'] != self._synchronization_id_by_instance[instance_id]
                ):
                    del self._synchronization_id_by_instance[instance_id]
                    return
                if primary_account_id in self._synchronization_listeners:
                    asyncio.create_task(self._latency_service.on_deals_synchronized(instance_id))
                    if socket_instance:
                        socket_instance['synchronizationThrottler'].remove_synchronization_id(data['synchronizationId'])

                    def run_on_deals_synchronized(listener: SynchronizationListener):
                        return lambda: listener.on_deals_synchronized(instance_index, data['synchronizationId'])

                    for listener in self._synchronization_listeners[primary_account_id]:
                        await self._process_event(
                            run_on_deals_synchronized(listener),
                            f'{primary_account_id}:{instance_index}:on_deals_synchronized',
                        )

                if instance_id in self._update_events:
                    self._update_events[instance_id] = list(
                        map(
                            lambda packet: lambda: self._process_synchronization_packet(packet),
                            self._update_events[instance_id],
                        )
                    )
                    if primary_account_id in self._event_queues:
                        self._event_queues[primary_account_id] = (
                            self._update_events[instance_id] + self._event_queues[primary_account_id]
                        )
                        del self._update_events[instance_id]
                    else:
                        self._event_queues[primary_account_id] = self._update_events[instance_id]
                        del self._update_events[instance_id]
                        asyncio.create_task(self._call_account_events(primary_account_id))

            elif data['type'] == 'orderSynchronizationFinished':
                if 'synchronizationId' in data and (
                    instance_id not in self._synchronization_id_by_instance
                    or data['synchronizationId'] != self._synchronization_id_by_instance[instance_id]
                ):
                    return
                if primary_account_id in self._synchronization_listeners:
                    for listener in self._synchronization_listeners[primary_account_id]:

                        def run_on_history_orders_synchronized(listener: SynchronizationListener):
                            return lambda: listener.on_history_orders_synchronized(
                                instance_index, data['synchronizationId']
                            )

                        await self._process_event(
                            run_on_history_orders_synchronized(listener),
                            f'{primary_account_id}:{instance_index}:on_history_orders_synchronized',
                        )

            elif data['type'] == 'status':
                if not self._connected_hosts.get(instance_id):
                    if (
                        instance_id in self._status_timers
                        and 'authenticated' in data
                        and data['authenticated']
                        and (
                            self._subscription_manager.is_disconnected_retry_mode(data['accountId'], instance_number)
                            or not self._subscription_manager.is_account_subscribing(data['accountId'], instance_number)
                        )
                    ):
                        self._subscription_manager.cancel_subscribe(data['accountId'] + ':' + str(instance_number))
                        await asyncio.sleep(0.01)
                        self._logger.info(
                            f'it seems like we are not connected to a '
                            + 'running API server yet, retrying subscription for account '
                            + instance_id
                        )
                        self.ensure_subscribe(data['accountId'], instance_number)
                else:
                    reset_disconnect_timer()

                    for listener in self._synchronization_listeners.get(primary_account_id, []):
                        await self._process_event(
                            lambda: listener.on_broker_connection_status_changed(
                                instance_index, bool(data['connected'])
                            ),
                            f'{primary_account_id}:{instance_index}:on_broker_connection_status_changed',
                        )

                    if 'healthStatus' in data:
                        if primary_account_id in self._synchronization_listeners:
                            for listener in self._synchronization_listeners[primary_account_id]:
                                await self._process_event(
                                    lambda: listener.on_health_status(instance_index, data['healthStatus']),
                                    f'{primary_account_id}:{instance_index}:on_health_status',
                                )

            elif data['type'] == 'downgradeSubscription':
                self._logger.info(
                    f'{primary_account_id}:{instance_index}: Market data subscriptions for symbol {data["symbol"]}'
                    f' were downgraded by the server due to rate limits. Updated subscriptions: '
                    f'{json.dumps(data["updates"]) if "updates" in data else ""}, removed subscriptions: '
                    f'{json.dumps(data["unsubscriptions"]) if "unsubscriptions" in data else ""}. Please read '
                    'https://metaapi.cloud/docs/client/rateLimiting/ for more details.'
                )

                if primary_account_id in self._synchronization_listeners:
                    for listener in self._synchronization_listeners[primary_account_id]:

                        def run_on_subscription_downgraded(listener: SynchronizationListener):
                            return lambda: listener.on_subscription_downgraded(
                                instance_index, data['symbol'], data.get('updates'), data.get('unsubscriptions')
                            )

                        await self._process_event(
                            run_on_subscription_downgraded(listener),
                            f'{primary_account_id}:{instance_index}:on_subscription_downgraded',
                        )

            elif data['type'] == 'specifications':
                if 'synchronizationId' in data and data[
                    'synchronizationId'
                ] != self._synchronization_id_by_instance.get(instance_id):
                    return

                specifications = data.get('specifications', [])
                removed_symbols = data.get('removedSymbols', [])

                for listener in self._synchronization_listeners.get(primary_account_id, []):
                    await self._process_event(
                        lambda: listener.on_symbol_specifications_updated(
                            instance_index, specifications, removed_symbols
                        ),
                        f'{primary_account_id}:{instance_index}:on_symbol_specifications_updated',
                    )

                    for specification in specifications:
                        await self._process_event(
                            lambda: listener.on_symbol_specification_updated(instance_index, specification),
                            f'{primary_account_id}:{instance_index}:on_symbol_specification_updated',
                        )

                    for removed_symbol in removed_symbols:
                        await self._process_event(
                            lambda: listener.on_symbol_specification_removed(instance_index, removed_symbol),
                            f'{primary_account_id}:{instance_index}:on_symbol_specification_removed',
                        )

            elif data['type'] == 'prices':
                if 'synchronizationId' in data and (
                    instance_id not in self._synchronization_id_by_instance
                    or data['synchronizationId'] != self._synchronization_id_by_instance[instance_id]
                ):
                    return
                prices = data.get('prices', [])
                candles = data.get('candles', [])
                ticks = data.get('ticks', [])
                books = data.get('books', [])
                if primary_account_id in self._synchronization_listeners:
                    equity = data.get('equity')
                    margin = data.get('margin')
                    free_margin = data.get('freeMargin')
                    margin_level = data.get('marginLevel')
                    account_currency_exchange_rate = data.get('accountCurrencyExchangeRate')

                    for listener in self._synchronization_listeners[primary_account_id]:
                        if len(prices):

                            def run_on_symbol_prices_updated(listener: SynchronizationListener):
                                return lambda: listener.on_symbol_prices_updated(
                                    instance_index,
                                    prices,
                                    equity,
                                    margin,
                                    free_margin,
                                    margin_level,
                                    account_currency_exchange_rate,
                                )

                            await self._process_event(
                                run_on_symbol_prices_updated(listener),
                                f'{primary_account_id}:{instance_index}:on_symbol_prices_updated',
                            )

                        if len(candles):

                            def run_on_candles_updated(listener: SynchronizationListener):
                                return lambda: listener.on_candles_updated(
                                    instance_index,
                                    candles,
                                    equity,
                                    margin,
                                    free_margin,
                                    margin_level,
                                    account_currency_exchange_rate,
                                )

                            await self._process_event(
                                run_on_candles_updated(listener),
                                f'{primary_account_id}:{instance_index}:on_candles_updated',
                            )

                        if len(ticks):

                            def run_on_ticks_updated(listener: SynchronizationListener):
                                return lambda: listener.on_ticks_updated(
                                    instance_index,
                                    ticks,
                                    equity,
                                    margin,
                                    free_margin,
                                    margin_level,
                                    account_currency_exchange_rate,
                                )

                            await self._process_event(
                                run_on_ticks_updated(listener),
                                f'{primary_account_id}:{instance_index}:on_ticks_updated',
                            )

                        if len(books):

                            def run_on_books_updated(listener: SynchronizationListener):
                                return lambda: listener.on_books_updated(
                                    instance_index,
                                    books,
                                    equity,
                                    margin,
                                    free_margin,
                                    margin_level,
                                    account_currency_exchange_rate,
                                )

                            await self._process_event(
                                run_on_books_updated(listener),
                                f'{primary_account_id}:{instance_index}:on_books_updated',
                            )

                for price in prices:
                    if primary_account_id in self._synchronization_listeners:
                        for listener in self._synchronization_listeners[primary_account_id]:

                            def run_on_symbol_price_updated(listener: SynchronizationListener):
                                return lambda: listener.on_symbol_price_updated(instance_index, price)

                            await self._process_event(
                                run_on_symbol_price_updated(listener),
                                f'{primary_account_id}:{instance_index}:on_symbol_price_updated',
                            )

                for price in prices:
                    if 'timestamps' in price:
                        price['timestamps']['clientProcessingFinished'] = datetime.now()

                        for listener in self._latency_listeners:

                            def run_on_symbol_price(listener: LatencyListener):
                                return lambda: listener.on_symbol_price(
                                    primary_account_id, price['symbol'], price['timestamps']
                                )

                            await self._process_event(
                                run_on_symbol_price(listener), f'{primary_account_id}:{instance_index}:on_symbol_price'
                            )

        except Exception as err:
            self._logger.error('Failed to process incoming synchronization packet ' + string_format_error(err))

    async def _process_event(self, callable, label: str, throw_error: bool = False):
        start_time = datetime.now().timestamp()
        is_long_event = False
        is_event_done = False

        async def check_long_event():
            await asyncio.sleep(1)
            if not is_event_done:
                nonlocal is_long_event
                is_long_event = True
                self._logger.warn(f'{label}: event is taking more than 1 second to process')

        asyncio.create_task(check_long_event())
        try:
            await callable()
        except Exception as err:
            if throw_error:
                raise err

            self._logger.error(f'{label}: event failed with error ' + string_format_error(err))
        is_event_done = True
        if is_long_event:
            self._logger.warn(f'{label}: finished in {math.floor(datetime.now().timestamp() - start_time)} seconds')

    async def _fire_reconnected(self, instance_number: int, socket_instance_index: int, region: str):
        try:
            reconnect_listeners = []
            for listener in self._reconnect_listeners:
                if (
                    listener['accountId'] in self._socket_instances_by_accounts[instance_number]
                    and self._socket_instances_by_accounts[instance_number][listener['accountId']]
                    == socket_instance_index
                    and self.get_account_region(listener['accountId']) == region
                ):
                    reconnect_listeners.append(listener)

            for synchronization_id in list(self._synchronization_flags.keys()):
                account_id = self._synchronization_flags[synchronization_id]['accountId']
                if (
                    account_id in self._socket_instances_by_accounts[instance_number]
                    and self._socket_instances_by_accounts[instance_number][account_id] == socket_instance_index
                    and self._synchronization_flags[synchronization_id]['instanceNumber'] == instance_number
                    and account_id in self._regions_by_accounts
                    and self._regions_by_accounts[account_id]['region'] == region
                ):
                    del self._synchronization_flags[synchronization_id]

            reconnect_account_ids = list(map(lambda listener: listener['accountId'], reconnect_listeners))
            self._subscription_manager.on_reconnected(instance_number, socket_instance_index, reconnect_account_ids)
            self._packet_orderer.on_reconnected(reconnect_account_ids)

            for listener in reconnect_listeners:

                async def on_reconnected_task(listener):
                    try:
                        await listener['listener'].on_reconnected(region, instance_number)
                    except Exception as err:
                        self._logger.error(f'Failed to notify reconnect listener ' + string_format_error(err))

                asyncio.create_task(on_reconnected_task(listener))
        except Exception as err:
            self._logger.error(f'Failed to process reconnected event ' + string_format_error(err))

    def _get_socket_instance_by_account(self, account_id: str, instance_number: int):
        region = self.get_account_region(account_id)
        if (
            instance_number in self._socket_instances_by_accounts
            and account_id in self._socket_instances_by_accounts[instance_number]
        ):
            return self._socket_instances[region][instance_number][
                self._socket_instances_by_accounts[instance_number][account_id]
            ]
        else:
            return None

    async def get_url_settings(self, instance_number: int, region: str):
        if self._url:
            return {'url': self._url, 'isSharedClientApi': True}

        url_settings = await self._domain_client.get_settings()

        def get_url(hostname):
            return f'https://{hostname}.{region}-{chr(97 + int(instance_number))}.{url_settings["domain"]}'

        if self._use_shared_client_api:
            url = get_url(self._hostname)
        else:
            url = get_url(url_settings['hostname'])

        is_shared_client_api = url == get_url(self._hostname)
        return {'url': url, 'isSharedClientApi': is_shared_client_api}

    async def _get_server_url(self, instance_number: int, socket_instance_index: int, region: str):
        if self._url:
            return self._url

        while self.socket_instances[region][instance_number][socket_instance_index]['connected']:
            try:
                url_settings = await self.get_url_settings(instance_number, region)
                url = url_settings['url']
                is_shared_client_api = url_settings['isSharedClientApi']

                log_message = (
                    'Connecting MetaApi websocket client to the MetaApi server '
                    + f'via {url} {"shared" if is_shared_client_api else "dedicated"} server.'
                )
                if self._first_connect and not is_shared_client_api:
                    log_message += (
                        ' Please note that it can take up to 3 minutes for your dedicated server to '
                        + 'start for the first time. During this time it is OK if you see some connection errors.'
                    )
                    self._first_connect = False
                self._logger.info(log_message)
                return url
            except Exception as err:
                self._logger.error('Failed to retrieve server URL ' + string_format_error(err))
                await asyncio.sleep(1)

    def _throttle_request(self, type, account_id, instance_number, time_in_ms):
        self._last_requests_time[instance_number] = self._last_requests_time.get(instance_number, {})
        self._last_requests_time[instance_number][type] = self._last_requests_time[instance_number].get(type, {})
        last_time = (
            self._last_requests_time[instance_number][type][account_id]
            if account_id in self._last_requests_time[instance_number][type]
            else None
        )
        if last_time is None or last_time < datetime.now().timestamp() - time_in_ms / 1000:
            self._last_requests_time[instance_number][type][account_id] = datetime.now().timestamp()
            return last_time is not None
        return False

    def _refresh_account_region(self, account_id: str):
        if account_id in self._regions_by_accounts:
            self._regions_by_accounts[account_id]['lastUsed'] = datetime.now().timestamp()

    async def _create_socket_instance_by_account(self, account_id: str, instance_number: int):
        region = self.get_account_region(account_id)
        if account_id not in self._socket_instances_by_accounts[instance_number]:
            socket_instance_index = None
            while self._subscribe_lock and (
                (
                    date(self._subscribe_lock['recommendedRetryTime']).timestamp() > datetime.now().timestamp()
                    and len(self.subscribed_account_ids(instance_number, None, region))
                    < self._subscribe_lock['lockedAtAccounts']
                )
                or (
                    date(self._subscribe_lock['lockedAtTime']).timestamp() + self._subscribe_cooldown_in_seconds
                    > datetime.now().timestamp()
                    and len(self.subscribed_account_ids(instance_number, None, region))
                    >= self._subscribe_lock['lockedAtAccounts']
                )
            ):
                await asyncio.sleep(1)
            for index in range(len(self._socket_instances[region][instance_number])):
                account_counter = len(self.get_assigned_accounts(instance_number, index, region))
                instance = self.socket_instances[region][instance_number][index]
                if instance['subscribeLock']:
                    if instance['subscribeLock']['type'] == 'LIMIT_ACCOUNT_SUBSCRIPTIONS_PER_USER_PER_SERVER' and (
                        date(instance['subscribeLock']['recommendedRetryTime']).timestamp() > datetime.now().timestamp()
                        or len(self.subscribed_account_ids(instance_number, index, region))
                        >= instance['subscribeLock']['lockedAtAccounts']
                    ):
                        continue
                    if instance['subscribeLock']['type'] == 'LIMIT_ACCOUNT_SUBSCRIPTIONS_PER_SERVER' and (
                        date(instance['subscribeLock']['recommendedRetryTime']).timestamp() > datetime.now().timestamp()
                        and len(self.subscribed_account_ids(instance_number, index, region))
                        >= instance['subscribeLock']['lockedAtAccounts']
                    ):
                        continue
                if account_counter < self._max_accounts_per_instance:
                    socket_instance_index = index
                    break
            if socket_instance_index is None:
                socket_instance_index = len(self._socket_instances[region][instance_number])
                await self.connect(instance_number, region)
            self._socket_instances_by_accounts[instance_number][account_id] = socket_instance_index

    def _clear_account_cache_job(self):
        try:
            date = datetime.now().timestamp()
            for replica_id in list(self._regions_by_accounts.keys()):
                data = self._regions_by_accounts.get(replica_id)
                if data and data['connections'] == 0 and date - data['lastUsed'] > 2 * 60 * 60:
                    primary_account_id = self._accounts_by_replica_id[replica_id]
                    replicas = (
                        self._account_replicas[primary_account_id].values()
                        if primary_account_id in self._account_replicas
                        else []
                    )
                    for replica in replicas:
                        del self._accounts_by_replica_id[replica]
                        del self._regions_by_accounts[replica]
                    del self._account_replicas[primary_account_id]
                    self._logger.debug(f"{primary_account_id}: removed expired account replicas data")
        except Exception as err:
            self._logger.error(f'Failed to process clear regions job ' + string_format_error(err))

    def _clear_inactive_sync_data_job(self):
        date = datetime.now().timestamp()
        for synchronization_id in self._synchronization_hashes:
            if self._synchronization_hashes[synchronization_id]['lastUpdated'] < date - 30 * 60:
                del self._synchronization_hashes[synchronization_id]
