import asyncio
from contextlib import suppress
from datetime import datetime
from typing import List, Dict

import socketio

from ...logger import LoggerManager


class LatencyService:
    """Service for managing account replicas based on region latency."""

    def __init__(self, websocket_client, token: str, connect_timeout: float):
        """Constructs latency service instance.

        Args:
            websocket_client: MetaApi websocket client.
            token: Authorization token.
            connect_timeout: Websocket connect timeout in seconds.
        """
        self._websocket_client = websocket_client
        self._token = token
        self._connect_timeout = connect_timeout
        self._latency_cache = {}
        self._connected_instances_cache = {}
        self._synchronized_instances_cache = {}
        self._refresh_promises_by_region = {}
        self._wait_connect_promises: Dict[str, asyncio.Future] = {}
        self._logger = LoggerManager.get_logger('LatencyService')

        async def refresh_latency_task():
            while True:
                await asyncio.sleep(15 * 60)
                await self._refresh_latency_job()

        self._refresh_region_latency_interval = asyncio.create_task(refresh_latency_task())

    def stop(self):
        """Stops the service."""
        self._refresh_region_latency_interval.cancel()

    @property
    def regions_sorted_by_latency(self) -> List[str]:
        """Returns the list of regions sorted by latency.

        Returns:
            A list of regions sorted by latency.
        """
        regions = self._latency_cache.keys()
        return sorted(regions, key=lambda region: self._latency_cache[region])

    def on_disconnected(self, instance_id: str):
        """Invoked when an instance has been disconnected.

        Args:
            instance_id: Instance id.
        """
        try:
            account_id = self._get_account_id_from_instance(instance_id)
            disconnected_region = self._get_region_from_instance(instance_id)
            self._disconnect_instance(instance_id)
            instances = self._get_account_instances(account_id)
            if True not in list(map(lambda instance: self._connected_instances_cache[instance], instances)):
                regions = self._get_account_regions(account_id)
                for region in list(filter(lambda region: region != disconnected_region, regions)):
                    self._subscribe_account_replica(account_id, region)
        except Exception as err:
            self._logger.error(f'Failed to process on_disconnected event for instance {instance_id}', err)

    def on_unsubscribe(self, account_id: str):
        """Invoked when an account has been unsubscribed.

        Args:
            account_id: Account id.
        """
        try:
            region = self._websocket_client.get_account_region(account_id)
            if account_id in self._websocket_client.accounts_by_replica_id:
                primary_account_id = self._websocket_client.accounts_by_replica_id[account_id]
                instances = self._get_account_instances(primary_account_id)
                for instance_id in list(
                    filter(lambda instance_id: instance_id.startswith(f'{primary_account_id}:{region}'), instances)
                ):
                    self._disconnect_instance(instance_id)
        except Exception as err:
            self._logger.error(f'Failed to process on_unsubscribe event for instance {account_id}', err)

    async def on_connected(self, instance_id: str):
        """Invoked when an instance has been connected.

        Args:
            instance_id: Instance id.
        """
        try:
            self._connected_instances_cache[instance_id] = True
            account_id = self._get_account_id_from_instance(instance_id)
            region = self._get_region_from_instance(instance_id)
            if region not in self._latency_cache:
                await self._refresh_latency(region)
            instances = self.get_active_account_instances(account_id)
            synchronized_instances = self.get_synchronized_account_instances(account_id)
            regions = list(map(lambda instance: self._get_region_from_instance(instance), instances))
            if len(instances) > 1 and not len(synchronized_instances):
                regions_to_disconnect = list(
                    filter(lambda sorted_region: sorted_region in regions, self.regions_sorted_by_latency)
                )[1:]
                for region_item in regions_to_disconnect:
                    asyncio.create_task(
                        self._websocket_client.unsubscribe(
                            self._websocket_client.account_replicas[account_id][region_item]
                        )
                    )
                    asyncio.create_task(self._websocket_client.unsubscribe_account_region(account_id, region_item))
            if account_id in self._wait_connect_promises:
                self._wait_connect_promises[account_id].set_result(True)
                del self._wait_connect_promises[account_id]
        except Exception as err:
            self._logger.error(f'Failed to process on_connected event for instance {instance_id}', err)

    async def on_deals_synchronized(self, instance_id: str):
        """Invoked when an instance has been synchronized.

        Args:
            instance_id: Instance id.
        """
        try:
            self._synchronized_instances_cache[instance_id] = True
            account_id = self._get_account_id_from_instance(instance_id)
            region = self._get_region_from_instance(instance_id)
            if region not in self._latency_cache:
                await self._refresh_latency(region)
            instances = self.get_synchronized_account_instances(account_id)
            regions = set()

            for instance in instances:
                regions.add(self._get_region_from_instance(instance))

            if len(instances) > 1:
                regions_to_disconnect = list(
                    filter(lambda sorted_region: sorted_region in regions, self.regions_sorted_by_latency)
                )[1:]
                for region_item in regions_to_disconnect:
                    asyncio.create_task(
                        self._websocket_client.unsubscribe(
                            self._websocket_client.account_replicas[account_id][region_item]
                        )
                    )
                    asyncio.create_task(self._websocket_client.unsubscribe_account_region(account_id, region_item))
        except Exception as err:
            self._logger.error(f'Failed to process on_deals_synchronized event for instance {instance_id}', err)

    def get_active_account_instances(self, account_id: str) -> List[str]:
        """Returns the list of currently connected account instances.

        Args:
            account_id: Account id.

        Returns:
            List of connected account instances.
        """
        return list(
            filter(
                lambda instance_id: instance_id in self._connected_instances_cache
                and self._connected_instances_cache[instance_id],
                self._get_account_instances(account_id),
            )
        )

    def get_synchronized_account_instances(self, account_id: str):
        """Returns the list of currently synchronized account instances.

        Args:
            account_id: Account id.

        Returns:
            List of synchronized account instances.
        """
        return list(
            filter(
                lambda instance_id: instance_id in self._synchronized_instances_cache
                and self._synchronized_instances_cache[instance_id],
                self._get_account_instances(account_id),
            )
        )

    async def wait_connected_instance(self, account_id: str) -> str:
        """Waits for connected instance.

        Args:
            account_id: Account id.

        Returns:
            Instance id.
        """
        instances = self.get_active_account_instances(account_id)
        if not len(instances):
            if account_id not in self._wait_connect_promises:
                self._wait_connect_promises[account_id] = asyncio.Future()
            await self._wait_connect_promises[account_id]
            instances = self.get_active_account_instances(account_id)
        return instances[0]

    def _get_account_instances(self, account_id: str):
        return list(
            filter(lambda instance_id: instance_id.startswith(f'{account_id}:'), self._connected_instances_cache.keys())
        )

    def _get_account_regions(self, account_id: str):
        regions = []
        instances = self._get_account_instances(account_id)
        for instance in instances:
            region = self._get_region_from_instance(instance)
            if region not in regions:
                regions.append(region)
        return regions

    @staticmethod
    def _get_account_id_from_instance(instance_id: str):
        return instance_id.split(':')[0]

    @staticmethod
    def _get_region_from_instance(instance_id: str):
        return instance_id.split(':')[1]

    def _disconnect_instance(self, instance_id: str):
        self._connected_instances_cache[instance_id] = False
        if instance_id in self._synchronized_instances_cache:
            self._synchronized_instances_cache[instance_id] = False

    def _subscribe_account_replica(self, account_id: str, region: str):
        instance_id = self._websocket_client.account_replicas.get(account_id, {}).get(region)
        if instance_id:
            self._websocket_client.ensure_subscribe(instance_id, 0)
            self._websocket_client.ensure_subscribe(instance_id, 1)

    async def _refresh_latency_job(self):
        for region in self._latency_cache.keys():
            await self._refresh_latency(region)

        # For every account, switch to a better region if such exists
        account_ids = []
        for instance_id in list(
            filter(
                lambda instance_id: self._connected_instances_cache[instance_id], self._connected_instances_cache.keys()
            )
        ):
            account_id = self._get_account_id_from_instance(instance_id)
            if account_id not in account_ids:
                account_ids.append(account_id)

        sorted_regions = self.regions_sorted_by_latency

        for account_id in account_ids:
            account_regions = self._get_account_regions(account_id)
            active_instances = self.get_active_account_instances(account_id)
            if len(active_instances) == 1:
                active_instance = active_instances[0]
                active_region = self._get_region_from_instance(active_instance)
                account_best_regions = list(filter(lambda region: region in account_regions, sorted_regions))
                if account_best_regions[0] != active_region:
                    self._subscribe_account_replica(account_id, account_best_regions[0])

    async def _refresh_latency(self, region: str):
        if region in self._refresh_promises_by_region:
            return await self._refresh_promises_by_region[region]
        future = asyncio.Future()
        self._refresh_promises_by_region[region] = future
        server_url = await self._websocket_client.get_url_settings(0, region)
        start_date = datetime.now().timestamp()

        socket_instance = socketio.AsyncClient(reconnection=True, request_timeout=self._connect_timeout)
        url = f'{server_url["url"]}?auth-token={self._token}&protocol=3'
        while not socket_instance.connected:
            with suppress(Exception):
                await asyncio.wait_for(socket_instance.connect(url, socketio_path='ws'), timeout=self._connect_timeout)

        self._latency_cache[region] = datetime.now().timestamp() - start_date
        await socket_instance.disconnect()
        future.set_result(True)
        del self._refresh_promises_by_region[region]
