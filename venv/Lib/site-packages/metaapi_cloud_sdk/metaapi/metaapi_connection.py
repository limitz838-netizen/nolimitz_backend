import asyncio
from abc import abstractmethod
from random import random
from typing import Optional, Union

from typing_extensions import TypedDict

from .metatrader_account_model import MetatraderAccountModel
from ..clients.metaapi.metaapi_websocket_client import MetaApiWebsocketClient
from ..clients.metaapi.reconnect_listener import ReconnectListener
from ..clients.metaapi.synchronization_listener import SynchronizationListener
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


class Options(TypedDict):
    """MetaApiConnection options."""

    refreshReplicasMaxDelayInMs: Optional[float]
    """Max delay before refreshing replicas delay. Default is 6 * 60 * 60 * 1000"""


# Should extend from MetaApiOpts, but circular dependency
class Config(TypedDict):
    """MetaApi options for connections."""

    connections: Optional[Options]
    """MetaApi connections options. Only for tests. Will be ignored when set in SDK."""


class MetaApiConnection(SynchronizationListener, ReconnectListener):
    """Exposes MetaApi MetaTrader API connection to consumers."""

    def __init__(
        self,
        options: Config,
        websocket_client: MetaApiWebsocketClient,
        account: MetatraderAccountModel,
        application: str = None,
    ):
        """Initializes MetaApi MetaTrader Api connection.

        Args:
            websocket_client: MetaApi websocket client.
            account: MetaTrader account id to connect to.
            application: Application to use.
        """
        self._options = options
        super().__init__()
        self._websocket_client = websocket_client
        self._account = account
        self._logger = LoggerManager.get_logger('MetaApiConnection')
        self._application = application
        self._state_by_instance_index = {}
        self._opened = False
        self._closed = False
        self._refresh_tasks = {}

    @abstractmethod
    async def connect(self, instance_id: str):
        """Opens the connection. Can only be called the first time, next calls will be ignored.

        Args:
            instance_id: Connection instance id.

        Returns:
            A coroutine resolving when the connection is opened
        """
        pass

    @abstractmethod
    async def close(self, instance_id: str):
        """Closes the connection. The instance of the class should no longer be used after this method is invoked.

        Args:
            instance_id: Connection instance id.
        """
        pass

    def on_reconnected(self, region: str, instance_number: int):
        """Invoked when connection to MetaApi websocket API restored after a disconnect.

        Args:
            region: Reconnected region.
            instance_number: Reconnected instance number.

        Returns:
            A coroutine which resolves when connection to MetaApi websocket API restored after a disconnect.
        """
        pass

    @property
    def account(self) -> MetatraderAccountModel:
        """Returns MetaApi account.

        Returns:
            MetaApi account.
        """
        return self._account

    @property
    def application(self) -> str:
        """Returns connection application.

        Returns:
            Connection application.
        """
        return self._application

    def schedule_refresh(self, region: str):
        """Schedules the refresh task.

        Args:
            region: Replica region.
        """
        if not self._refresh_tasks.get(region):
            delay_in_ms = self._options.get('connections', {}).get('refreshReplicasMaxDelayInMs')
            delay = random() * (delay_in_ms / 1000 if delay_in_ms is not None else 6 * 60 * 60)

            async def refresh_task():
                await asyncio.sleep(delay)
                await self._refresh_replicas()

            asyncio.create_task(refresh_task())

    def cancel_refresh(self, region: str):
        """Cancels the scheduled refresh task.

        Args:
            region: Replica region.
        """
        if region in self._refresh_tasks:
            self._refresh_tasks[region].cancel()
            del self._refresh_tasks[region]

    async def _refresh_replicas(self):
        for task in self._refresh_tasks:
            if task != asyncio.current_task():
                task.cancel()

        self._refresh_tasks = {}
        old_replicas = {}

        for replica in self._account.replicas:
            old_replicas[replica.region] = replica.id

        new_replicas = {}
        is_account_updated = False

        try:
            await self._account.reload()
            is_account_updated = True

            for replica in self._account.replicas:
                new_replicas[replica.region] = replica.id
        except Exception as error:
            if error.__class__.__name__ == 'NotFoundException':
                if self._connection_registry:
                    self._connection_registry.close_all_instances(self._account.id)

        if is_account_updated:
            deleted_replicas = {}
            added_replicas = {}

            for key in old_replicas:
                if new_replicas.get(key) != old_replicas[key]:
                    deleted_replicas[key] = old_replicas[key]

            for key in new_replicas:
                if new_replicas[key] != old_replicas.get(key):
                    added_replicas[key] = new_replicas[key]

            if len(deleted_replicas):
                for replica_id in deleted_replicas.values():
                    self._websocket_client.on_account_deleted(replica_id)

            if len(deleted_replicas) or len(added_replicas):
                new_replicas[self._account.region] = self._account.id
                self._websocket_client.update_account_cache(self._account.id, new_replicas)

                for region, instance in self._account.account_regions.items():
                    if not self._options.get('region') or self._options.get('region') == region:
                        self._websocket_client.ensure_subscribe(instance, 0)
                        self._websocket_client.ensure_subscribe(instance, 1)

    async def synchronize(self, instance_id: str):
        """Closes the connection. The instance of the class should no longer be used after this method is invoked.

        Args:
            instance_id: Connection instance id.
        """
        pass

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

    def _check_is_connection_active(self):
        if not self._opened:
            raise Exception('This connection has not been initialized yet, please invoke await connection.connect()')

        if self._closed:
            raise Exception('This connection has been closed, please create a new connection')
