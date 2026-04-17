import asyncio
from datetime import datetime
from random import uniform
from typing import List
from .latency_service import LatencyService

from ..error_handler import TooManyRequestsException
from ...logger import LoggerManager
from ...metaapi.models import date, format_error, string_format_error


class SubscriptionManager:
    """Subscription manager to handle account subscription logic."""

    def __init__(self, websocket_client, metaapi):
        """Initializes the subscription manager.

        Args:
            websocket_client: Websocket client to use for sending requests.
        """
        self._websocket_client = websocket_client
        self._latency_service: LatencyService = websocket_client.latency_service
        self._metaapi = metaapi
        self._subscriptions = {}
        self._awaiting_resubscribe = {}
        self._subscription_state = {}
        self._logger = LoggerManager.get_logger('SubscriptionManager')
        self._timeout_error_counter = {}
        self._recently_deleted_accounts = {}

    def is_account_subscribing(self, account_id: str, instance_number: int = None):
        """Returns whether an account is currently subscribing.

        Args:
            account_id: Id of the MetaTrader account.
            instance_number: Instance index number.
        """
        if instance_number is not None:
            return account_id + ':' + str(instance_number) in self._subscriptions.keys()
        else:
            for key in self._subscriptions.keys():
                if key.startswith(account_id):
                    return True
            return False

    def is_disconnected_retry_mode(self, account_id: str, instance_number: int):
        """Returns whether an instance is in disconnected retry mode.

        Args:
            account_id: Id of the MetaTrader account.
            instance_number: Instance index number.
        """
        instance_id = account_id + ':' + str(instance_number or 0)
        return (
            self._subscriptions[instance_id]['isDisconnectedRetryMode'] if instance_id in self._subscriptions else False
        )

    def is_subscription_active(self, account_id: str) -> bool:
        """Returns whether an account subscription is active.

        Args:
            account_id: account id.

        Returns:
            Instance actual subscribe state.
        """
        return account_id in self._subscription_state

    def subscribe(self, account_id: str, instance_number):
        """Subscribes to the Metatrader terminal events
        (see https://metaapi.cloud/docs/client/websocket/api/subscribe/).

        Args:
            account_id: Id of the MetaTrader account to subscribe to.
            instance_number: Instance index number.

        Returns:
            A coroutine which resolves when subscription started.
        """
        self._subscription_state[account_id] = True
        packet = {'type': 'subscribe'}
        if instance_number is not None:
            packet['instanceIndex'] = instance_number
        return self._websocket_client.rpc_request(account_id, packet)

    async def schedule_subscribe(self, account_id: str, instance_number: int = None, is_disconnected_retry_mode=False):
        """Schedules to send subscribe requests to an account until cancelled.

        Args:
            account_id: Id of the MetaTrader account.
            instance_number: Instance index number.
            is_disconnected_retry_mode: Whether to start subscription in disconnected retry mode. Subscription
                task in disconnected mode will be immediately replaced when the status packet is received.
        """
        instance_id = account_id + ':' + str(instance_number or 0)
        if instance_id not in self._subscriptions:
            self._subscriptions[instance_id] = {
                'shouldRetry': True,
                'task': None,
                'wait_task': None,
                'future': None,
                'isDisconnectedRetryMode': is_disconnected_retry_mode,
            }
            subscribe_retry_interval_in_seconds = 3
            while self._subscriptions[instance_id]['shouldRetry']:

                async def subscribe_task():
                    try:
                        self._logger.debug(f"{account_id}:{instance_number}: running subscribe task")
                        await self.subscribe(account_id, instance_number)
                    except Exception as err:
                        if isinstance(err, TooManyRequestsException):
                            socket_instance_index = self._websocket_client.socket_instances_by_accounts[
                                instance_number
                            ][account_id]
                            if err.metadata['type'] == 'LIMIT_ACCOUNT_SUBSCRIPTIONS_PER_USER':
                                self._log_subscription_error(account_id, f'{instance_id}: Failed to subscribe',
                                                             err)
                            if err.metadata['type'] in [
                                'LIMIT_ACCOUNT_SUBSCRIPTIONS_PER_USER',
                                'LIMIT_ACCOUNT_SUBSCRIPTIONS_PER_SERVER',
                                'LIMIT_ACCOUNT_SUBSCRIPTIONS_PER_USER_PER_SERVER',
                            ]:
                                del self._websocket_client.socket_instances_by_accounts[instance_number][account_id]
                                asyncio.create_task(
                                    self._websocket_client.lock_socket_instance(
                                        instance_number,
                                        socket_instance_index,
                                        self._websocket_client.get_account_region(account_id),
                                        err.metadata,
                                    )
                                )
                            else:
                                nonlocal subscribe_retry_interval_in_seconds
                                retry_time = date(err.metadata['recommendedRetryTime']).timestamp()
                                if datetime.now().timestamp() + subscribe_retry_interval_in_seconds < retry_time:
                                    await asyncio.sleep(
                                        retry_time - datetime.now().timestamp() - subscribe_retry_interval_in_seconds
                                    )
                        else:
                            self._log_subscription_error(account_id, f'{instance_id}: Failed to subscribe',
                                                         err)

                            if err.__class__.__name__ == 'NotFoundException':
                                self.refresh_account(account_id)

                            if err.__class__.__name__ == 'TimeoutException':
                                main_account_id = self._websocket_client.accounts_by_replica_id.get(account_id)
                                if main_account_id:
                                    region = self._websocket_client.get_account_region(account_id)
                                    connected_instances = (
                                        self._latency_service.get_active_account_instances(
                                            main_account_id
                                        )
                                    )

                                    is_instance_main = False

                                    for instance in connected_instances:
                                        if instance.startswith(f"{main_account_id}:{region}"):
                                            is_instance_main = True
                                            break

                                    if not is_instance_main:
                                        self._timeout_error_counter[account_id] = self._timeout_error_counter.get(
                                            account_id, 0
                                        )
                                        self._timeout_error_counter[account_id] += 1

                                        if self._timeout_error_counter[account_id] > 4:
                                            self._timeout_error_counter[account_id] = 0
                                            self.refresh_account(account_id)

                self._subscriptions[instance_id]['task'] = asyncio.create_task(subscribe_task())
                await asyncio.wait({self._subscriptions[instance_id]['task']})
                if not self._subscriptions[instance_id]['shouldRetry']:
                    break
                retry_interval = subscribe_retry_interval_in_seconds
                subscribe_retry_interval_in_seconds = min(subscribe_retry_interval_in_seconds * 2, 300)
                subscribe_future = asyncio.Future()

                async def subscribe_task():
                    await asyncio.sleep(retry_interval)
                    subscribe_future.set_result(True)

                self._subscriptions[instance_id]['wait_task'] = asyncio.create_task(subscribe_task())
                self._subscriptions[instance_id]['future'] = subscribe_future
                result = await self._subscriptions[instance_id]['future']
                self._subscriptions[instance_id]['future'] = None
                if not result:
                    break
            del self._subscriptions[instance_id]

    async def unsubscribe(self, account_id: str, instance_number: int):
        """Unsubscribe from account (see https://metaapi.cloud/docs/client/websocket/api/synchronizing/unsubscribe).

        Args:
            account_id: Id of the MetaTrader account to retrieve symbol price for.
            instance_number: Instance index number.

        Returns:
            A coroutine which resolves when socket is unsubscribed."""
        self.cancel_account(account_id)
        if account_id in self._subscription_state:
            del self._subscription_state[account_id]
        return await self._websocket_client.rpc_request(
            account_id, {'type': 'unsubscribe', 'instanceIndex': instance_number}
        )

    def cancel_subscribe(self, instance_id: str):
        """Cancels active subscription tasks for an instance id.

        Args:
            instance_id: Instance id to cancel subscription task for.
        """
        if instance_id in self._subscriptions:
            subscription = self._subscriptions[instance_id]
            if subscription['future'] and not subscription['future'].done():
                subscription['future'].set_result(False)
                subscription['wait_task'].cancel()
            if subscription['task']:
                subscription['task'].cancel()
            subscription['shouldRetry'] = False

    def cancel_account(self, account_id):
        """Cancels active subscription tasks for an account.

        Args:
            account_id: Account id to cancel subscription tasks for.
        """
        for instance_id in list(filter(lambda key: key.startswith(account_id), self._subscriptions.keys())):
            self.cancel_subscribe(instance_id)

        for instance_number in self._awaiting_resubscribe.keys():
            if account_id in self._awaiting_resubscribe[instance_number]:
                del self._awaiting_resubscribe[instance_number][account_id]

        if account_id in self._timeout_error_counter:
            del self._timeout_error_counter[account_id]

    def on_timeout(self, account_id: str, instance_number: int = None):
        """Invoked on account timeout.

        Args:
            account_id: Id of the MetaTrader account.
            instance_number: Instance index number.
        """
        region = self._websocket_client.get_account_region(account_id)
        if account_id in self._websocket_client.socket_instances_by_accounts[
            instance_number
        ] and self._websocket_client.connected(
            instance_number, self._websocket_client.socket_instances_by_accounts[instance_number][account_id], region
        ):
            self._logger.debug(
                f'{account_id}:{instance_number}: scheduling subscribe subscribe because of account ' + 'timeout'
            )
            asyncio.create_task(self.schedule_subscribe(account_id, instance_number, is_disconnected_retry_mode=True))

    async def on_disconnected(self, account_id: str, instance_number: int = None):
        """Invoked when connection to MetaTrader terminal terminated.

        Args:
            account_id: Id of the MetaTrader account.
            instance_number: Instance index number.
        """
        await asyncio.sleep(uniform(1, 5))
        if (
            instance_number in self._websocket_client.socket_instances_by_accounts
            and account_id in self._websocket_client.socket_instances_by_accounts[instance_number]
        ):
            self._logger.debug(f'{account_id}:{instance_number}: scheduling subscribe because account disconnected')
            asyncio.create_task(self.schedule_subscribe(account_id, instance_number, is_disconnected_retry_mode=True))

    def on_reconnected(self, instance_number: int, socket_instance_index: int, reconnect_account_ids: List[str]):
        """Invoked when connection to MetaApi websocket API restored after a disconnect.

        Args:
            instance_number: Instance index number.
            socket_instance_index: Socket instance index.
            reconnect_account_ids: Account ids to reconnect.
        """
        if instance_number not in self._awaiting_resubscribe:
            self._awaiting_resubscribe[instance_number] = {}

        async def wait_resubscribe(account_id):
            if account_id not in self._awaiting_resubscribe[instance_number]:
                self._awaiting_resubscribe[instance_number][account_id] = True
                while self.is_account_subscribing(account_id, instance_number):
                    await asyncio.sleep(1)
                await asyncio.sleep(uniform(0, 5))
                if account_id in self._awaiting_resubscribe[instance_number]:
                    del self._awaiting_resubscribe[instance_number][account_id]
                    self._logger.debug(
                        f'{account_id}:{instance_number}: scheduling subscribe because account reconnected'
                    )
                    asyncio.create_task(self.schedule_subscribe(account_id, instance_number))

        socket_instances_by_accounts = self._websocket_client.socket_instances_by_accounts[instance_number]
        for instance_id in self._subscriptions.keys():
            account_id = instance_id.split(':')[0]
            if (
                account_id in socket_instances_by_accounts
                and socket_instances_by_accounts[account_id] == socket_instance_index
            ):
                self.cancel_subscribe(instance_id)

        for account_id in reconnect_account_ids:
            asyncio.create_task(wait_resubscribe(account_id))

    def refresh_account(self, account_id: str):
        """Schedules a task to refresh the account data.

        Args:
            account_id: Account id.
        """
        main_account_id = self._websocket_client.accounts_by_replica_id.get(account_id)
        if main_account_id:
            registry = self._metaapi._connection_registry
            region = self._websocket_client.get_account_region(account_id)
            if region:
                rpc_connection = registry.rpc_connections.get(main_account_id)
                if rpc_connection:
                    rpc_connection.schedule_refresh(region)

                streaming_connection = registry.streaming_connections.get(main_account_id)
                if streaming_connection:
                    streaming_connection.schedule_refresh(region)

    def _log_subscription_error(self, account_id: str, message, error):
        primary_account_id = self._websocket_client.accounts_by_replica_id[account_id]
        msg = message + ' ' + string_format_error(error)
        if len(self._latency_service.get_synchronized_account_instances(primary_account_id)):
            self._logger.debug(msg)
        else:
            self._logger.error(msg)
