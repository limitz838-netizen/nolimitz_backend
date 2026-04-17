from abc import ABC, abstractmethod
from typing import List, Dict

from ..clients.metaapi.metatrader_account_client import UpdatedMetatraderAccountReplicaDto, MetatraderAccountReplicaDto


class MetatraderAccountReplicaModel(ABC):
    """Implements a MetaTrader account replica entity."""

    @property
    @abstractmethod
    def id(self) -> str:
        """Returns account replica id.

        Returns:
            Account replica id.
        """

    @property
    @abstractmethod
    def magic(self) -> int:
        """Returns MetaTrader magic to place trades using.

        Returns:
            MetaTrader magic to place trades using.
        """

    @property
    @abstractmethod
    def state(self) -> str:
        """Returns account replica deployment state. One of CREATED, DEPLOYING, DEPLOYED, UNDEPLOYING,
        UNDEPLOYED, DELETING.

        Returns:
            MetaTrader magic to place trades using.
        """
        return self._data['state']

    @property
    @abstractmethod
    def connection_status(self) -> str:
        """Returns terminal & broker connection status, one of CONNECTED, DISCONNECTED, DISCONNECTED_FROM_BROKER

        Returns:
            Terminal & broker connection status.
        """
        return self._data['connectionStatus']

    @property
    @abstractmethod
    def metadata(self) -> Dict:
        """Returns extra information which can be stored together with your account replica.

        Returns:
            Extra information which can be stored together with your account replica.
        """

    @property
    @abstractmethod
    def tags(self) -> List[str]:
        """Returns user-defined account replica tags.

        Returns:
            User-defined account replica tags.
        """

    @property
    @abstractmethod
    def resource_slots(self) -> int:
        """Returns number of resource slots to allocate to account replica. Allocating extra resource slots
        results in better account performance under load which is useful for some applications. E.g. if you have many
        accounts copying the same strategy via CooyFactory API, then you can increase resourceSlots to get a lower
        trade copying latency. Please note that allocating extra resource slots is a paid option. Please note that high
        reliability accounts use redundant infrastructure, so that each resource slot for a high reliability account
        is billed as 2 standard resource slots. Default is 1.

        Returns:
            Number of resource slots to allocate to account replica.
        """
        return self._data.get('resourceSlots')

    @property
    @abstractmethod
    def copyfactory_resource_slots(self) -> int:
        """Returns the number of CopyFactory 2 resource slots to allocate to account replica. Allocating extra resource
        slots results in lower trade copying latency. Please note that allocating extra resource slots is a paid option.
        Please also note that CopyFactory 2 uses redundant infrastructure so that each CopyFactory resource slot is
        billed as 2 standard resource slots. You will be billed for CopyFactory 2 resource slots only if you have
        added your account replica to CopyFactory 2 by specifying copyFactoryRoles field. Default is 1.

        Returns:
            Number of CopyFactory 2 resource slots to allocate to account.
        """

    @property
    @abstractmethod
    def reliability(self) -> str:
        """Returns reliability value. Possible values are regular and high.

        Returns:
            Account replica reliability value.
        """

    @property
    @abstractmethod
    def region(self) -> str:
        """Returns account region.

        Returns:
            Account region value.
        """
        return self._data['region']

    @property
    @abstractmethod
    def primary_account(self):
        """Returns primary MetaTrader account of the replica.

        Returns:
            Primary MetaTrader account of the replica.
        """

    @abstractmethod
    def update_data(self, data: MetatraderAccountReplicaDto):
        """Updates replica data.

        Args:
            data: MetaTrader account replica data.
        """

    @abstractmethod
    async def remove(self):
        """Removes MetaTrader account replica. Cloud account transitions to DELETING state.
        It takes some time for an account to be eventually deleted. Self-hosted account is deleted immediately.

        Returns:
            A coroutine resolving when account replica is scheduled for deletion.
        """

    @abstractmethod
    async def deploy(self):
        """Schedules account replica for deployment. It takes some time for API server to be started and
        account replica to reach the DEPLOYED state.

        Returns:
            A coroutine resolving when account replica is scheduled for deployment.
        """

    @abstractmethod
    async def undeploy(self):
        """Schedules account replica for undeployment. It takes some time for API server to be stopped and account to
        reach the UNDEPLOYED state.

        Returns:
            A coroutine resolving when account is scheduled for undeployment.
        """

    @abstractmethod
    async def redeploy(self):
        """Schedules account replica for redeployment. It takes some time for API server to be restarted and account
        replica to reach the DEPLOYED state.

        Returns:
            A coroutine resolving when account replica is scheduled for redeployment.
        """

    @abstractmethod
    async def increase_reliability(self):
        """Increases MetaTrader account replica reliability. The account replica will be temporary stopped to perform
        this action.

        Returns:
            A coroutine resolving when account replica reliability is increased.
        """

    @abstractmethod
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

    @abstractmethod
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

    @abstractmethod
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

    @abstractmethod
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

    @abstractmethod
    async def update(self, account: UpdatedMetatraderAccountReplicaDto):
        """Updates MetaTrader account replica data.

        Args:
            account: MetaTrader account replica update.

        Returns:
            A coroutine resolving when account is updated.
        """
