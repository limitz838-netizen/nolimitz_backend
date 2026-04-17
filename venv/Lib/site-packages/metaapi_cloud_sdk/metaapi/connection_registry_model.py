from abc import ABC, abstractmethod
from datetime import datetime

from .history_storage import HistoryStorage
from ..metaapi.metatrader_account_model import MetatraderAccountModel


class ConnectionRegistryModel(ABC):
    """Defines interface for a connection registry class."""

    @abstractmethod
    def connect_streaming(
        self, account: MetatraderAccountModel, history_storage: HistoryStorage, history_start_time: datetime = None
    ):
        """Creates and returns a new account connection if it doesn't exist, otherwise returns old.

        Args:
            account: MetaTrader account to connect to.
            history_storage: Terminal history storage.
            history_start_time: History start time.

        Returns:
            A coroutine resolving with account connection.
        """

    @abstractmethod
    async def remove_streaming(self, account: MetatraderAccountModel):
        """Removes a streaming connection from registry.

        Args:
            account: MetaTrader account to remove from registry.
        """

    @abstractmethod
    def connect_rpc(self, account: MetatraderAccountModel):
        """Creates and returns a new account connection if it doesn't exist, otherwise returns old.

        Args:
            account: MetaTrader account to connect to.

        Returns:
            A coroutine resolving with account connection.
        """

    @abstractmethod
    async def remove_rpc(self, account: MetatraderAccountModel):
        """Removes an RPC connection from registry.

        Args:
            account: MetaTrader account to remove from registry.
        """

    @abstractmethod
    def remove(self, account_id: str):
        """Removes an account from registry.

        Args:
            account_id: MetaTrader account id to remove.
        """

    @property
    @abstractmethod
    def application(self) -> str:
        """Returns application type.

        Returns:
            Application type.
        """

    @abstractmethod
    def close_all_instances(self, account_id: str) -> str:
        """Returns application type.

        Returns:
            Application type.
        """
