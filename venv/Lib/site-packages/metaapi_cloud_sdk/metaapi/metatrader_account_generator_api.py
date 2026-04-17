from .metatrader_account_credentials import MetatraderAccountCredentials
from ..clients.metaapi.metatrader_account_generator_client import (
    MetatraderAccountGeneratorClient,
    NewMT4DemoAccount,
    NewMT5DemoAccount,
)


class MetatraderAccountGeneratorApi:
    """Exposes MetaTrader account generator API logic to the consumers."""

    def __init__(self, metatrader_account_generator_client: MetatraderAccountGeneratorClient):
        """Initializes a MetaTrader account generator API instance.

        Args:
            metatrader_account_generator_client: MetaTrader account generator REST API client.
        """
        self._metatrader_account_generator_client = metatrader_account_generator_client

    async def create_mt4_demo_account(
        self, account: NewMT4DemoAccount, profile_id: str = None
    ) -> MetatraderAccountCredentials:
        """Creates new MetaTrader 4 demo account.
        See https://metaapi.cloud/docs/provisioning/api/generateAccount/createMT4DemoAccount/

        Args:
            account: Account to create.
            profile_id: Id of the provisioning profile that will be used as the basis for creating this account.

        Returns:
            A coroutine resolving with MetaTrader account credentials entity.
        """
        mt_account = await self._metatrader_account_generator_client.create_mt4_demo_account(account, profile_id)
        return MetatraderAccountCredentials(mt_account)

    async def create_mt5_demo_account(
        self, account: NewMT5DemoAccount, profile_id: str = None
    ) -> MetatraderAccountCredentials:
        """Creates new MetaTrader 5 demo account.
        See https://metaapi.cloud/docs/provisioning/api/generateAccount/createMT5DemoAccount/

        Args:
            account: Account to create.
            profile_id: Id of the provisioning profile that will be used as the basis for creating this account.

        Returns:
            A coroutine resolving with MetaTrader account credentials entity.
        """
        mt_account = await self._metatrader_account_generator_client.create_mt5_demo_account(account, profile_id)
        return MetatraderAccountCredentials(mt_account)
