import asyncio
from asyncio import Future
from typing import Dict, List, Coroutine, Any

from .equity_balance_listener import EquityBalanceListener
from ..domain_client import DomainClient
from ...models import random_id
from .... import MetaApi
from ....clients.metaapi.synchronization_listener import SynchronizationListener
from ....logger import LoggerManager
from ....metaapi.models import MetatraderSymbolPrice, MetatraderAccountInformation
from ....metaapi.streaming_metaapi_connection_instance import StreamingMetaApiConnectionInstance


class EquityBalanceStreamManager:
    """Manager for handling equity balance event listeners."""

    def __init__(self, domain_client: DomainClient, metaapi: MetaApi):
        """Constructs equity balance event listener manager instance.

        Args:
            domain_client: Domain client.
            metaapi: MetaApi SDK instance.
        """
        self._domain_client = domain_client
        self._metaapi = metaapi
        self._equity_balance_listeners: Dict[str, Dict[str, EquityBalanceListener]] = {}
        self._accounts_by_listener_id: Dict[str, str] = {}
        self._equity_balance_connections: Dict[str, StreamingMetaApiConnectionInstance] = {}
        self._equity_balance_caches = {}
        self._account_synchronization_flags: Dict[str, bool] = {}
        self._pending_initialization_resolves: Dict[str, List[Future]] = {}
        self._retry_interval_in_seconds = 1
        self._logger = LoggerManager.get_logger('EquityBalanceStreamManager')

    def get_account_listeners(self, account_id: str) -> Dict[str, EquityBalanceListener]:
        """Returns listeners for account.

        Args:
            account_id: Account id to return listeners for.

        Returns:
            Dictionary of account equity balance event listeners.
        """
        if account_id not in self._equity_balance_listeners:
            self._equity_balance_listeners[account_id] = {}
        return self._equity_balance_listeners[account_id]

    async def add_equity_balance_listener(
        self, listener: EquityBalanceListener, account_id: str
    ) -> Coroutine[str, Any, None]:
        """Adds an equity balance event listener.

        Args:
            listener: Equity balance event listener.
            account_id: Account id.

        Returns:
            Listener id.
        """
        if account_id not in self._equity_balance_caches:
            self._equity_balance_caches[account_id] = {
                'balance': None,
                'equity': None,
                'pendingInitalizationResolves': [],
            }
        cache = self._equity_balance_caches[account_id]
        connection: StreamingMetaApiConnectionInstance = None
        retry_interval_in_seconds = self._retry_interval_in_seconds

        def get_account_listeners():
            return self.get_account_listeners(account_id)

        pending_initialization_resolves = self._pending_initialization_resolves
        synchronization_flags = self._account_synchronization_flags

        async def process_equity_balance_event(equity=None, balance=None):
            if account_id in self._equity_balance_caches:
                if equity != cache['equity'] or (balance and balance != cache['balance']):
                    cache['equity'] = equity
                    if balance:
                        cache['balance'] = balance
                    if cache['equity'] is not None and cache['balance'] is not None:
                        for account_listener in self.get_account_listeners(account_id).values():
                            await account_listener.on_equity_or_balance_updated(
                                {'equity': cache['equity'], 'balance': cache['balance']}
                            )

        class EquityBalanceStreamListener(SynchronizationListener):
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
                        f"equity balance listener for account {account_id}"
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
                        f"equity balance listener for account {account_id}"
                        f"{err}"
                    )

            async def on_symbol_price_updated(self, instance_index: str, price: MetatraderSymbolPrice):
                try:
                    if account_id in pending_initialization_resolves:
                        for promise in pending_initialization_resolves[account_id]:
                            promise.set_result(True)
                            del pending_initialization_resolves[account_id]
                except Exception as err:
                    for account_listener in get_account_listeners().values():
                        asyncio.create_task(account_listener.on_error(err))
                    self._logger.error(
                        "Error processing on_symbol_price_updated event for"
                        f"equity balance listener for account {account_id}"
                        f"{err}"
                    )
                # price data only contains equity
                await process_equity_balance_event(price['equity'])

            async def on_account_information_updated(
                self, instance_index: str, account_information: MetatraderAccountInformation
            ):
                await process_equity_balance_event(account_information['equity'], account_information['balance'])

        listener_id = random_id(10)
        account_listeners = self.get_account_listeners(account_id)
        account_listeners[listener_id] = listener
        self._accounts_by_listener_id[listener_id] = account_id
        is_deployed = False
        account = await self._metaapi.metatrader_account_api.get_account(account_id)
        while not is_deployed:
            try:
                await account.wait_deployed()
                is_deployed = True
            except Exception as err:
                asyncio.create_task(listener.on_error(err))
                self._logger.error(f'Error wait for account {account_id} to deploy, retrying', err)
                await asyncio.sleep(retry_interval_in_seconds)
                retry_interval_in_seconds = min(retry_interval_in_seconds * 2, 300)

        if account_id not in self._equity_balance_connections:
            retry_interval_in_seconds = self._retry_interval_in_seconds
            connection = account.get_streaming_connection()
            self._equity_balance_connections[account_id] = connection
            sync_listener = EquityBalanceStreamListener()
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
                        'Error configuring equity balance stream listener ' + f'for account {account_id}, retrying', err
                    )
                    await asyncio.sleep(retry_interval_in_seconds)
                    retry_interval_in_seconds = min(retry_interval_in_seconds * 2, 300)

            retry_interval_in_seconds = self._retry_interval_in_seconds
        else:
            connection = self._equity_balance_connections[account_id]
            if not connection.health_monitor.health_status['synchronized']:
                if account_id not in self._pending_initialization_resolves:
                    self._pending_initialization_resolves[account_id] = []
                initialize_promise = Future()
                self._pending_initialization_resolves[account_id].append(initialize_promise)
                await initialize_promise
        return listener_id

    def remove_equity_balance_listener(self, listener_id: str):
        """Removes equity balance event listener by id.

        Args:
            listener_id: Listener id.
        """
        if listener_id in self._accounts_by_listener_id:
            account_id = self._accounts_by_listener_id[listener_id]
            if account_id in self._account_synchronization_flags:
                del self._account_synchronization_flags[account_id]
            if listener_id in self._accounts_by_listener_id:
                del self._accounts_by_listener_id[listener_id]
            if (
                account_id in self._equity_balance_listeners
                and listener_id in self._equity_balance_listeners[account_id]
            ):
                del self._equity_balance_listeners[account_id][listener_id]
            if account_id in self._equity_balance_connections and not len(
                self._equity_balance_listeners[account_id].keys()
            ):
                asyncio.create_task(self._equity_balance_connections[account_id].close())
                del self._equity_balance_connections[account_id]
