import asyncio
from datetime import datetime, timedelta
from typing import List, Dict

from .metatrader_account_model import MetatraderAccountModel
from .metatrader_account_replica_model import MetatraderAccountReplicaModel
from .models import date
from ..clients.metaapi.metatrader_account_client import (
    MetatraderAccountReplicaDto,
    UpdatedMetatraderAccountReplicaDto,
    MetatraderAccountClient,
)
from ..clients.timeout_exception import TimeoutException


class MetatraderAccountReplica(MetatraderAccountReplicaModel):
    """Implements a MetaTrader account replica entity."""

    def __init__(
        self,
        data: MetatraderAccountReplicaDto,
        primary_account: MetatraderAccountModel,
        metatrader_account_client: MetatraderAccountClient,
    ):
        """Constructs a MetaTrader account replica entity.

        Args:
            data: MetaTrader account replica data.
            primary_account: primary MetaTrader account.
            metatrader_account_client: MetaTrader account REST API client.
        """
        self._data = data
        self._primary_account = primary_account
        self._metatrader_account_client = metatrader_account_client

    @property
    def id(self) -> str:
        """Returns account replica id.

        Returns:
            Unique account replica id.
        """
        return self._data['_id']

    @property
    def state(self) -> str:
        """Returns current account replica state. One of CREATED, DEPLOYING, DEPLOYED, DEPLOY_FAILED, UNDEPLOYING,
        UNDEPLOYED, UNDEPLOY_FAILED, DELETING, DELETE_FAILED, REDEPLOY_FAILED, DRAFT.

        Returns:
            Current account replica state.
        """
        return self._data['state']

    @property
    def magic(self) -> int:
        """Returns MetaTrader magic to place trades using.

        Returns:
            MetaTrader magic to place trades using.
        """
        return self._data['magic']

    @property
    def connection_status(self) -> str:
        """Returns terminal & broker connection status, one of CONNECTED, DISCONNECTED, DISCONNECTED_FROM_BROKER.

        Returns:
            Terminal & broker connection status.
        """
        return self._data['connectionStatus']

    @property
    def quote_streaming_interval_in_seconds(self) -> str:
        """Returns quote streaming interval in seconds.

        Returns:
            Quote streaming interval in seconds.
        """
        return self._data['quoteStreamingIntervalInSeconds']

    @property
    def symbol(self) -> str:
        """Returns symbol provided by broker.

        Returns:
            Any symbol provided by broker.
        """
        return self._data['symbol']

    @property
    def reliability(self) -> str:
        """Returns reliability value. Possible values are regular and high.

        Returns:
            Account replica reliability value.
        """
        return self._data['reliability']

    @property
    def tags(self) -> List[str]:
        """Returns user-defined account replica tags.

        Returns:
            User-defined account replica tags.
        """
        return self._data.get('tags')

    @property
    def metadata(self) -> Dict:
        """Returns extra information which can be stored together with your account replica.

        Returns:
            Extra information which can be stored together with your account replica.
        """
        return self._data.get('metadata')

    @property
    def resource_slots(self) -> int:
        """Returns number of resource slots to allocate to account replica. Allocating extra resource slots
        results in better account performance under load which is useful for some applications. E.g. if you have many
        accounts copying the same strategy via CooyFactory API, then you can increase resourceSlots to get a lower
        trade copying latency. Please note that allocating extra resource slots is a paid option. Please note that high
        reliability accounts use redundant infrastructure, so that each resource slot for a high reliability account
        is billed as 2 standard resource slots.

        Returns:
            Number of resource slots to allocate to account replica.
        """
        return self._data.get('resourceSlots')

    @property
    def copyfactory_resource_slots(self) -> int:
        """Returns the number of CopyFactory 2 resource slots to allocate to account replica. Allocating extra resource
        slots results in lower trade copying latency. Please note that allocating extra resource slots is a paid option.
        Please also note that CopyFactory 2 uses redundant infrastructure so that each CopyFactory resource slot is
        billed as 2 standard resource slots. You will be billed for CopyFactory 2 resource slots only if you have
        added your account replica to CopyFactory 2 by specifying copyFactoryRoles field.

        Returns:
            Number of CopyFactory 2 resource slots to allocate to account.
        """
        return self._data.get('copyFactoryResourceSlots')

    @property
    def region(self) -> str:
        """Returns account region.

        Returns:
            Account region value.
        """
        return self._data['region']

    @property
    def created_at(self) -> datetime:
        """Returns the time account was created at, in ISO format.

        Returns:
            The time account was created at, in ISO format.
        """
        return date(self._data['createdAt'])

    @property
    def primary_account_from_dto(self) -> dict:
        """Returns primary MetaTrader account of the replica from DTO.

        Returns:
            Primary MetaTrader account of the replica from DTO.
        """
        return self._data['primaryAccount']

    @property
    def primary_account(self) -> MetatraderAccountModel:
        """Returns primary MetaTrader account of the replica.

        Returns:
            Primary MetaTrader account of the replica.
        """
        return self._primary_account

    def update_data(self, data: MetatraderAccountReplicaDto):
        """Updates account replica data.

        Args:
            data: MetaTrader account replica data.
        """
        self._data = data

    async def remove(self):
        """Removes a trading account replica and stops the API server serving the replica.

        Returns:
            A coroutine resolving when account replica is scheduled for deletion.
        """
        await self._metatrader_account_client.delete_account_replica(self.primary_account.id, self.id)
        try:
            await self._primary_account.reload()
        except Exception as err:
            if err.__class__.__name__ != 'NotFoundException':
                raise err

    async def deploy(self):
        """Starts API server and trading terminal for trading account replica.
        This request will be ignored if the replica is already deployed

        Returns:
            A coroutine resolving when account replica is scheduled for deployment.
        """
        await self._metatrader_account_client.deploy_account_replica(self.primary_account.id, self.id)
        await self._primary_account.reload()

    async def undeploy(self):
        """Stops API server and trading terminal for trading account replica.
        The request will be ignored if trading account replica is already undeployed.

        Returns:
            A coroutine resolving when account is scheduled for undeployment.
        """
        await self._metatrader_account_client.undeploy_account_replica(self.primary_account.id, self.id)
        await self._primary_account.reload()

    async def redeploy(self):
        """Redeploys trading account replica. This is equivalent to undeploy immediately followed by deploy.

        Returns:
            A coroutine resolving when account replica is scheduled for redeployment.
        """
        await self._metatrader_account_client.redeploy_account_replica(self.primary_account.id, self.id)
        await self._primary_account.reload()

    async def increase_reliability(self):
        """Increases trading account reliability in order to increase the expected account uptime.
        The account will be temporary stopped to perform this action.
        Not that increasing reliability is a paid option.

        Returns:
            A coroutine resolving when account replica reliability is increased.
        """
        await self._metatrader_account_client.increase_reliability(self.id)
        await self._primary_account.reload()

    async def wait_deployed(self, timeout_in_seconds=300, interval_in_milliseconds=1000):
        """Waits until API server has finished deployment and account replica reached the DEPLOYED state.

        Args:
            timeout_in_seconds: Wait timeout in seconds, default is 5m.
            interval_in_milliseconds: Interval between account replica reloads while waiting for a change, default
            is 1s.

        Returns:
            A coroutine which resolves when account replica is deployed.

        Raises:
            TimeoutException: If account replica has not reached the DEPLOYED state within timeout allowed.
        """
        start_time = datetime.now()
        await self._primary_account.reload()
        while self.state != 'DEPLOYED' and (start_time + timedelta(seconds=timeout_in_seconds) > datetime.now()):
            await self._delay(interval_in_milliseconds)
            await self._primary_account.reload()
        if self.state != 'DEPLOYED':
            raise TimeoutException('Timed out waiting for account replica ' + self.id + ' to be deployed')

    async def wait_undeployed(self, timeout_in_seconds=300, interval_in_milliseconds=1000):
        """Waits until API server has finished undeployment and account replica reached the UNDEPLOYED state.

        Args:
            timeout_in_seconds: Wait timeout in seconds, default is 5m.
            interval_in_milliseconds: Interval between account replica reloads while waiting for a change, default
            is 1s.

        Returns:
            A coroutine which resolves when account replica is undeployed.

        Raises:
            TimeoutException: If account replica has not reached the UNDEPLOYED state within timeout allowed.
        """
        start_time = datetime.now()
        await self._primary_account.reload()
        while self.state != 'UNDEPLOYED' and (start_time + timedelta(seconds=timeout_in_seconds) > datetime.now()):
            await self._delay(interval_in_milliseconds)
            await self._primary_account.reload()
        if self.state != 'UNDEPLOYED':
            raise TimeoutException('Timed out waiting for account replica ' + self.id + ' to be undeployed')

    async def wait_removed(self, timeout_in_seconds=300, interval_in_milliseconds=1000):
        """Waits until account replica has been deleted.

        Args:
            timeout_in_seconds: Wait timeout in seconds, default is 5m.
            interval_in_milliseconds: Interval between account replica reloads while waiting for a change, default
            is 1s.

        Returns:
            A coroutine which resolves when account replica is deleted.

        Raises:
            TimeoutException: If account replica was not deleted within timeout allowed.
        """
        start_time = datetime.now()
        await self._primary_account.reload()
        while (
            (start_time + timedelta(seconds=timeout_in_seconds)) > datetime.now()
            and self.region in self._primary_account.account_regions
            and self._primary_account.account_regions[self.region] == self.id
        ):
            await self._delay(interval_in_milliseconds)
            await self._primary_account.reload()
        if (
            self.region in self._primary_account.account_regions
            and self._primary_account.account_regions[self.region] == self.id
        ):
            raise TimeoutException('Timed out waiting for account replica ' + self.id + ' to be deleted')

    async def wait_connected(self, timeout_in_seconds=300, interval_in_milliseconds=1000):
        """Waits until API server has connected to the terminal and terminal has connected to the broker.

        Args:
            timeout_in_seconds: Wait timeout in seconds, default is 5m
            interval_in_milliseconds: Interval between account replica reloads while waiting for a change, default
            is 1s.

        Returns:
            A coroutine which resolves when API server is connected to the broker.

        Raises:
            TimeoutException: If account replica has not connected to the broker within timeout allowed.
        """
        start_time = datetime.now()
        await self._primary_account.reload()
        while (
            self.connection_status != 'CONNECTED'
            and (start_time + timedelta(seconds=timeout_in_seconds)) > datetime.now()
        ):
            await self._delay(interval_in_milliseconds)
            await self._primary_account.reload()
        if self.connection_status != 'CONNECTED':
            raise TimeoutException('Timed out waiting for account replica ' + self.id + ' to connect to the broker')

    async def update(self, metatrader_account: UpdatedMetatraderAccountReplicaDto):
        """Updates trading account replica.

        Args:
            metatrader_account: MetaTrader updated account replica information.

        Returns:
            A coroutine resolving when account is updated.
        """
        await self._metatrader_account_client.update_account_replica(
            self._primary_account.id, self.id, metatrader_account
        )
        await self._primary_account.reload()

    async def _delay(self, timeout_in_milliseconds):
        await asyncio.sleep(timeout_in_milliseconds / 1000)
