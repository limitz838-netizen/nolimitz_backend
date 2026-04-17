import asyncio
import math
from collections import deque
from datetime import datetime
from functools import reduce
from typing import Dict
from typing import Optional, List

from typing_extensions import TypedDict

from ..options_validator import OptionsValidator
from ..timeout_exception import TimeoutException
from ...logger import LoggerManager
from ...metaapi.models import string_format_error


class SynchronizationThrottlerOpts(TypedDict, total=False):
    """Options for synchronization throttler."""

    maxConcurrentSynchronizations: Optional[int]
    """Amount of maximum allowed concurrent synchronizations."""
    queueTimeoutInSeconds: Optional[float]
    """Allowed time for a synchronization in queue."""
    synchronizationTimeoutInSeconds: Optional[float]
    """Time after which a synchronization slot is freed to be used by another synchronization."""


class SynchronizationThrottler:
    """Synchronization throttler used to limit the amount of concurrent synchronizations to prevent application
    from being overloaded due to excessive number of synchronization responses being sent."""

    def __init__(
        self,
        client,
        socket_instance_index: int,
        instance_number: int,
        region: str,
        opts: SynchronizationThrottlerOpts = None,
    ):
        """Initializes the synchronization throttler.

        Args:
            client: Websocket client.
            socket_instance_index: Index of socket instance that uses the throttler.
            instance_number: Instance index number.
            region: Server region.
            opts: Synchronization throttler options.
        """
        validator = OptionsValidator()
        opts: SynchronizationThrottlerOpts = opts or {}
        self._maxConcurrentSynchronizations = validator.validate_non_zero(
            opts.get('maxConcurrentSynchronizations'), 15, 'synchronizationThrottler.maxConcurrentSynchronizations'
        )
        self._queueTimeoutInSeconds = validator.validate_non_zero(
            opts.get('queueTimeoutInSeconds'), 300, 'synchronizationThrottler.queueTimeoutInSeconds'
        )
        self._synchronizationTimeoutInSeconds = validator.validate_non_zero(
            opts.get('synchronizationTimeoutInSeconds'), 10, 'synchronizationThrottler.synchronizationTimeoutInSeconds'
        )
        self._client = client
        self._region = region
        self._socket_instance_index = socket_instance_index
        self._synchronization_ids = {}
        self._accounts_by_synchronization_ids = {}
        self._synchronization_queue = deque([])
        self._remove_old_sync_ids_interval = None
        self._process_queue_interval = None
        self._instance_number = instance_number
        self._logger = LoggerManager.get_logger('SynchronizationThrottler')

    def start(self):
        """Initializes the synchronization throttler."""

        async def remove_old_sync_ids_interval():
            while True:
                await self._remove_old_sync_ids_job()
                await asyncio.sleep(1)

        async def process_queue_interval():
            while True:
                await self._process_queue_job()
                await asyncio.sleep(1)

        if not self._remove_old_sync_ids_interval:
            self._remove_old_sync_ids_interval = asyncio.create_task(remove_old_sync_ids_interval())
            self._process_queue_interval = asyncio.create_task(process_queue_interval())

    def stop(self):
        """Deinitializes the throttler."""
        if self._remove_old_sync_ids_interval:
            self._remove_old_sync_ids_interval.cancel()
            self._remove_old_sync_ids_interval = None
        if self._process_queue_interval:
            self._process_queue_interval.cancel()
            self._process_queue_interval = None

    async def _remove_old_sync_ids_job(self):
        now = datetime.now().timestamp()
        for key in list(self._synchronization_ids.keys()):
            if (now - self._synchronization_ids[key]) > self._synchronizationTimeoutInSeconds:
                del self._synchronization_ids[key]
        while (
            len(self._synchronization_queue)
            and (datetime.now().timestamp() - self._synchronization_queue[0]['queueTime']) > self._queueTimeoutInSeconds
        ):
            self._remove_from_queue(self._synchronization_queue[0]['synchronizationId'], 'timeout')
        self._advance_queue()
        await asyncio.sleep(1)

    def update_synchronization_id(self, synchronization_id: str):
        """Fills a synchronization slot with synchronization id.

        Args:
            synchronization_id: Synchronization id.
        """
        if synchronization_id in self._accounts_by_synchronization_ids:
            self._synchronization_ids[synchronization_id] = datetime.now().timestamp()

    @property
    def synchronizing_accounts(self) -> List[str]:
        """Returns the list of currently synchronizing account ids."""
        synchronizing_accounts = []
        for key in self._synchronization_ids:
            account_data = self._accounts_by_synchronization_ids.get(key)
            if account_data and (account_data['accountId'] not in synchronizing_accounts):
                synchronizing_accounts.append(account_data['accountId'])
        return synchronizing_accounts

    @property
    def active_synchronization_ids(self) -> List[str]:
        """Returns the list of currently active synchronization ids."""
        return list(self._accounts_by_synchronization_ids.keys())

    @property
    def max_concurrent_synchronizations(self) -> int:
        """Returns the amount of maximum allowed concurrent synchronizations."""
        calculated_max = max(
            math.ceil(
                len(
                    self._client.subscribed_account_ids(
                        self._instance_number, self._socket_instance_index, self._region
                    )
                )
                / 10
            ),
            1,
        )
        return min(calculated_max, self._maxConcurrentSynchronizations)

    @property
    def is_synchronization_available(self) -> bool:
        """Whether there are free slots for synchronization requests."""

        def reducer_func(acc, socket_instance):
            return acc + len(socket_instance['synchronizationThrottler'].synchronizing_accounts)

        if (
            reduce(reducer_func, self._client.socket_instances[self._region][self._instance_number], 0)
            >= self._maxConcurrentSynchronizations
        ):
            return False
        return len(self.synchronizing_accounts) < self.max_concurrent_synchronizations

    def remove_id_by_parameters(self, account_id: str, instance_index: int = None, host: str = None):
        """Removes synchronizations from queue and from the list by parameters.

        Args:
            account_id: Account id.
            instance_index: Account instance index.
            host: Account host name.
        """
        for key in list(self._accounts_by_synchronization_ids.keys()):
            if (
                self._accounts_by_synchronization_ids[key]['accountId'] == account_id
                and self._accounts_by_synchronization_ids[key]['instanceIndex'] == instance_index
                and self._accounts_by_synchronization_ids[key]['host'] == host
            ):
                self.remove_synchronization_id(key)

    def remove_synchronization_id(self, synchronization_id: str):
        """Removes synchronization id from slots and removes ids for the same account from the queue.

        Args:
            synchronization_id: Synchronization id.
        """
        if synchronization_id in self._accounts_by_synchronization_ids:
            account_id = self._accounts_by_synchronization_ids[synchronization_id]['accountId']
            instance_index = self._accounts_by_synchronization_ids[synchronization_id]['instanceIndex']
            host = self._accounts_by_synchronization_ids[synchronization_id]['host']
            for key in list(self._accounts_by_synchronization_ids.keys()):
                if (
                    self._accounts_by_synchronization_ids[key]['accountId'] == account_id
                    and self._accounts_by_synchronization_ids[key]['instanceIndex'] == instance_index
                    and self._accounts_by_synchronization_ids[key]['host'] == host
                ):
                    self._remove_from_queue(key, 'cancel')
                    del self._accounts_by_synchronization_ids[key]
        if synchronization_id in self._synchronization_ids:
            del self._synchronization_ids[synchronization_id]
        self._advance_queue()

    def on_disconnect(self):
        """Clears synchronization ids on disconnect."""
        for synchronization in self._synchronization_queue:
            if not synchronization['promise'].done():
                synchronization['promise'].set_result('cancel')
        self._synchronization_ids = {}
        self._accounts_by_synchronization_ids = {}
        self._synchronization_queue = deque([])
        self.stop()
        self.start()

    def _advance_queue(self):
        index = 0
        while (
            self.is_synchronization_available
            and len(self._synchronization_queue)
            and index < len(self._synchronization_queue)
        ):
            queue_item = self._synchronization_queue[index]
            if not queue_item['promise'].done():
                queue_item['promise'].set_result('synchronize')
                self.update_synchronization_id(queue_item['synchronizationId'])
            index += 1

    def _remove_from_queue(self, synchronization_id: str, result: str):
        for i in range(len(self._synchronization_queue)):
            sync_item = self._synchronization_queue[i]
            if sync_item['synchronizationId'] == synchronization_id and not sync_item['promise'].done():
                sync_item['promise'].set_result(result)
        self._synchronization_queue = deque(
            filter(lambda item: item['synchronizationId'] != synchronization_id, self._synchronization_queue)
        )

    async def _process_queue_job(self):
        try:
            while len(self._synchronization_queue):
                queue_item = self._synchronization_queue[0]
                await queue_item['promise']
                if (
                    len(self._synchronization_queue)
                    and self._synchronization_queue[0]['synchronizationId'] == queue_item['synchronizationId']
                ):
                    self._synchronization_queue.popleft()
        except Exception as err:
            self._logger.error('Error processing queue job ' + string_format_error(err))

    async def schedule_synchronize(self, account_id: str, request: Dict, hashes):
        """Schedules to send a synchronization request for account.

        Args:
            account_id: Account id.
            request: Request to send.
            hashes: Terminal state hashes.
        """
        synchronization_id = request['requestId']
        for key in list(self._accounts_by_synchronization_ids.keys()):
            if (
                self._accounts_by_synchronization_ids[key]['accountId'] == account_id
                and self._accounts_by_synchronization_ids[key]['instanceIndex'] == (request.get('instanceIndex'))
                and self._accounts_by_synchronization_ids[key]['host'] == (request.get('host'))
            ):
                self.remove_synchronization_id(key)
        self._accounts_by_synchronization_ids[synchronization_id] = {
            'accountId': account_id,
            'instanceIndex': request.get('instanceIndex'),
            'host': request.get('host'),
        }
        if not self.is_synchronization_available:
            request_resolve = asyncio.Future()
            self._synchronization_queue.append(
                {
                    'synchronizationId': synchronization_id,
                    'promise': request_resolve,
                    'queueTime': datetime.now().timestamp(),
                }
            )
            result = await request_resolve
            if result == 'cancel':
                return False
            elif result == 'timeout':
                raise TimeoutException(
                    f'Account {account_id} synchronization {synchronization_id} timed out in ' f'synchronization queue'
                )
        self.update_synchronization_id(synchronization_id)
        request['specificationsHashes'] = hashes['specificationsHashes']
        request['positionsHashes'] = hashes['positionsHashes']
        request['ordersHashes'] = hashes['ordersHashes']
        await self._client.rpc_request(account_id, request)
        return True
