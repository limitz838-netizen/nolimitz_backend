import asyncio
from datetime import datetime
from typing import Dict

from .connection_registry_model import ConnectionRegistryModel
from .history_storage import HistoryStorage
from .metatrader_account_model import MetatraderAccountModel
from .rpc_metaapi_connection import RpcMetaApiConnection
from .rpc_metaapi_connection_instance import RpcMetaApiConnectionInstance
from .streaming_metaapi_connection import StreamingMetaApiConnection
from .streaming_metaapi_connection_instance import StreamingMetaApiConnectionInstance
from .terminal_hash_manager import TerminalHashManager
from ..clients.metaapi.metaapi_websocket_client import MetaApiWebsocketClient


class ConnectionRegistry(ConnectionRegistryModel):
    """Manages account connections"""

    def __init__(
        self,
        options,
        meta_api_websocket_client: MetaApiWebsocketClient,
        terminal_hash_manager: TerminalHashManager,
        application: str = 'MetaApi',
        refresh_subscriptions_opts: dict = None,
    ):
        """Initializes a MetaTrader connection registry instance.

        Args:
            options: MetaApi options.
            meta_api_websocket_client: MetaApi websocket client.
            terminal_hash_manager: Client API client.
            application: Application type.
            refresh_subscriptions_opts: Subscriptions refresh options.
        """
        refresh_subscriptions_opts = refresh_subscriptions_opts or {}
        self._meta_api_websocket_client = meta_api_websocket_client
        self._terminal_hash_manager = terminal_hash_manager
        self._application = application
        self._refresh_subscriptions_opts = refresh_subscriptions_opts
        self._rpc_connections = {}
        self._rpc_connection_instances = {}
        self._streaming_connections = {}
        self._streaming_connection_instances = {}
        self._connection_locks = {}
        self._options = options

    def connect_streaming(
        self, account: MetatraderAccountModel, history_storage: HistoryStorage, history_start_time: datetime = None
    ) -> StreamingMetaApiConnectionInstance:
        """Creates and returns a new account connection if it doesn't exist, otherwise returns old.

        Args:
            account: MetaTrader account to connect to.
            history_storage: Terminal history storage.
            history_start_time: History start time.

        Returns:
            A coroutine resolving with account connection.
        """
        if account.id not in self._streaming_connections:
            self._streaming_connections[account.id] = StreamingMetaApiConnection(
                self._options,
                self._meta_api_websocket_client,
                self._terminal_hash_manager,
                account,
                history_storage,
                self,
                history_start_time,
                self._refresh_subscriptions_opts,
            )

        instance = StreamingMetaApiConnectionInstance(
            self._meta_api_websocket_client, self._streaming_connections[account.id]
        )
        self._streaming_connection_instances[account.id] = self._streaming_connection_instances.get(account.id, [])
        self._streaming_connection_instances[account.id].append(instance)

        return instance

    async def remove_streaming(self, account: MetatraderAccountModel):
        """Removes a streaming connection from registry.

        Args:
            account: MetaTrader account to remove from registry.
        """
        if account.id in self._streaming_connections:
            del self._streaming_connections[account.id]
            del self._streaming_connection_instances[account.id]
        if account.id not in self._rpc_connections:
            await self._close_last_connection(account)

    def connect_rpc(self, account: MetatraderAccountModel) -> RpcMetaApiConnectionInstance:
        """Creates and returns a new account connection if it doesn't exist, otherwise returns old.

        Args:
            account: MetaTrader account to connect to.

        Returns:
            A coroutine resolving with account connection.
        """
        if account.id not in self._rpc_connections:
            self._rpc_connections[account.id] = RpcMetaApiConnection(
                self._options, self._meta_api_websocket_client, account, self
            )

        instance = RpcMetaApiConnectionInstance(self._meta_api_websocket_client, self._rpc_connections[account.id])
        self._rpc_connection_instances[account.id] = self._rpc_connection_instances.get(account.id, [])
        self._rpc_connection_instances[account.id].append(instance)
        return instance

    async def remove_rpc(self, account: MetatraderAccountModel):
        """Removes an RPC connection from registry.

        Args:
            account: MetaTrader account to remove from registry.
        """
        if account.id in self._rpc_connections:
            del self._rpc_connections[account.id]
            del self._rpc_connection_instances[account.id]
        if account.id not in self._streaming_connections:
            await self._close_last_connection(account)

    def remove(self, account_id: str):
        """Removes an account from registry.

        Args:
            account_id: MetaTrader account id to remove.
        """
        if account_id in self._rpc_connections:
            del self._rpc_connections[account_id]
        if account_id in self._rpc_connection_instances:
            del self._rpc_connection_instances[account_id]
        if account_id in self._streaming_connections:
            del self._streaming_connections[account_id]
        if account_id in self._streaming_connection_instances:
            del self._streaming_connection_instances[account_id]

    @property
    def application(self) -> str:
        """Returns application type.

        Returns:
            Application type.
        """
        return self._application

    async def _close_last_connection(self, account: MetatraderAccountModel):
        account_regions = account.account_regions
        await asyncio.gather(
            *list(
                map(
                    lambda replica_id: self._meta_api_websocket_client.unsubscribe(replica_id), account_regions.values()
                )
            )
        )

    @property
    def streaming_connections(self) -> Dict[str, StreamingMetaApiConnection]:
        """Returns the dictionary of streaming connections."""
        return self._streaming_connections

    @property
    def rpc_connections(self) -> Dict[str, RpcMetaApiConnection]:
        """Returns the dictionary of rpc connections."""
        return self._rpc_connections

    def close_all_instances(self, account_id: str):
        """Closes all connection instances for an account.

        Args:
            account_id
        """
        if self._rpc_connection_instances.get(account_id):
            for instance in self._rpc_connection_instances[account_id]:
                asyncio.create_task(instance.close())

        if self._streaming_connection_instances.get(account_id):
            for instance in self._streaming_connection_instances[account_id]:
                asyncio.create_task(instance.close())
