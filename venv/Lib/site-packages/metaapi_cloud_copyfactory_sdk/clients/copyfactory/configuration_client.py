from copy import deepcopy
from typing import List, Union

from .copyfactory_models import (
    StrategyId,
    CopyFactoryStrategyUpdate,
    CopyFactorySubscriberUpdate,
    CopyFactorySubscriber,
    CopyFactoryStrategy,
    CopyFactoryPortfolioStrategy,
    CopyFactoryPortfolioStrategyUpdate,
    CopyFactoryCloseInstructions,
    GetStrategiesOptions,
    ApiVersion,
    GetPortfolioStrategiesOptions,
    ClassicPaginationList,
    GetSubscribersOptions,
    GetWebhooksOptions,
    Webhook,
    WebhookUpdate,
    PaginationStyle,
    NewWebhook,
    WebhookIdAndUrl
)
from ..domain_client import DomainClient
from ..metaapi_client import MetaApiClient
from ...models import random_id, convert_iso_time_to_date, format_request, date


class ConfigurationClient(MetaApiClient):
    """metaapi.cloud CopyFactory configuration API (trade copying configuration API) client (see
    https://metaapi.cloud/docs/copyfactory/)"""

    def __init__(self, domain_client: DomainClient):
        """Initializes CopyFactory configuration API client instance.

        Args:
            domain_client: Domain client.
        """
        super().__init__(domain_client)
        self._domain_client = domain_client

    async def generate_strategy_id(self) -> StrategyId:
        """Retrieves new unused strategy id. Method is accessible only with API access token. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/generateStrategyId/

        Returns:
            A coroutine resolving with strategy id generated.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('generate_strategy_id')
        opts = {
            'url': f"/users/current/configuration/unused-strategy-id",
            'method': 'GET',
            'headers': {'auth-token': self._token},
        }
        return await self._domain_client.request_copyfactory(opts)

    @staticmethod
    def generate_account_id() -> str:
        """Generates random account id.

        Returns:
            Account id.
        """
        return random_id(64)

    async def get_strategies_with_infinite_scroll_pagination(
        self, options: GetStrategiesOptions = None
    ) -> List[CopyFactoryStrategy]:
        """Retrieves CopyFactory copy trading strategies.
         See https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/getStrategies/

         Args:
             options: Options.

        Returns:
             A coroutine resolving with CopyFactory strategies found.
        """
        return await self._get_strategies('1', options)

    async def get_strategies_with_classic_pagination(
        self, options: GetStrategiesOptions = None
    ) -> ClassicPaginationList:
        """Retrieves CopyFactory copy trading strategies.
         See https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/getStrategies/

         Args:
             options: Options.

        Returns:
             A coroutine resolving with CopyFactory strategies found.
        """
        return await self._get_strategies('2', options)

    async def _get_strategies(self, api_version: ApiVersion, options: Union[GetStrategiesOptions, None]):
        """Retrieves CopyFactory copy trading strategies. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/getStrategies/

        Args:
            api_version: API version to use.
            options: Options.

        Returns:
            A coroutine resolving with CopyFactory strategies found.
        """
        if options is None:
            options = {}
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('get_strategies')
        opts = {
            'url': f"/users/current/configuration/strategies",
            'method': 'GET',
            'params': options,
            'headers': {'auth-token': self._token, 'api-version': api_version},
        }
        result = await self._domain_client.request_copyfactory(opts, True)
        convert_iso_time_to_date(result)
        return result

    async def get_strategy(self, strategy_id: str) -> CopyFactoryStrategy:
        """Retrieves CopyFactory copy trading strategy by id. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/getStrategy/

        Args:
            strategy_id: Trading strategy id.

        Returns:
            A coroutine resolving with CopyFactory strategy found.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('get_strategy')
        opts = {
            'url': f"/users/current/configuration/strategies/{strategy_id}",
            'method': 'GET',
            'headers': {'auth-token': self._token},
        }
        strategy = await self._domain_client.request_copyfactory(opts)
        convert_iso_time_to_date(strategy)
        return strategy

    async def update_strategy(self, strategy_id: str, strategy: CopyFactoryStrategyUpdate):
        """Updates a CopyFactory strategy. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/updateStrategy/

        Args:
            strategy_id: Copy trading strategy id.
            strategy: Trading strategy update.

        Returns:
            A coroutine resolving when strategy is updated.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('update_strategy')
        payload = deepcopy(strategy)
        format_request(payload)
        opts = {
            'url': f"/users/current/configuration/strategies/{strategy_id}",
            'method': 'PUT',
            'headers': {'auth-token': self._token},
            'body': payload,
        }
        return await self._domain_client.request_copyfactory(opts)

    async def remove_strategy(self, strategy_id: str, close_instructions: CopyFactoryCloseInstructions = None):
        """Deletes a CopyFactory strategy. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/removeStrategy/

        Args:
            strategy_id: Copy trading strategy id.
            close_instructions: Strategy close instructions.

        Returns:
            A coroutine resolving when strategy is removed.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('remove_strategy')
        opts = {
            'url': f"/users/current/configuration/strategies/{strategy_id}",
            'method': 'DELETE',
            'headers': {'auth-token': self._token},
        }
        if close_instructions is not None:
            format_request(close_instructions)
            opts['body'] = close_instructions
        return await self._domain_client.request_copyfactory(opts)

    async def get_portfolio_strategies_with_infinite_scroll_pagination(
        self, options: GetPortfolioStrategiesOptions = None
    ) -> List[CopyFactoryStrategy]:
        """Retrieves CopyFactory copy portfolio strategies. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/getPortfolioStrategies/

        Args:
            options: Options.

        Returns:
            A coroutine resolving with CopyFactory portfolio strategies found.
        """
        return await self._get_portfolio_strategies('1', options)

    async def get_portfolio_strategies_with_classic_pagination(
        self, options: GetPortfolioStrategiesOptions = None
    ) -> ClassicPaginationList:
        """Retrieves CopyFactory copy portfolio strategies. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/getPortfolioStrategies/

        Args:
            options: Options.

        Returns:
            A coroutine resolving with CopyFactory portfolio strategies found.
        """
        return await self._get_portfolio_strategies('2', options)

    async def _get_portfolio_strategies(
        self, api_version: ApiVersion, options: Union[GetPortfolioStrategiesOptions, None]
    ):
        """Retrieves CopyFactory copy portfolio strategies. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/getPortfolioStrategies/

        Args:
            api_version: API version to use.
            options: Options.

        Returns:
            A coroutine resolving with CopyFactory portfolio strategies found.
        """
        if options is None:
            options = {}
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('get_portfolio_strategies')
        opts = {
            'url': f"/users/current/configuration/portfolio-strategies",
            'method': 'GET',
            'params': options,
            'headers': {'auth-token': self._token, 'api-version': api_version},
        }
        result = await self._domain_client.request_copyfactory(opts, True)
        convert_iso_time_to_date(result)
        return result

    async def get_portfolio_strategy(self, portfolio_id: str) -> CopyFactoryPortfolioStrategy:
        """Retrieves a CopyFactory copy portfolio strategy by id. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/getPortfolioStrategy/

        Args:
            portfolio_id: Portfolio strategy id.

        Returns:
            A coroutine resolving with CopyFactory portfolio strategy found.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('get_portfolio_strategy')
        opts = {
            'url': f"/users/current/configuration/portfolio-strategies/{portfolio_id}",
            'method': 'GET',
            'headers': {'auth-token': self._token},
        }
        strategy = await self._domain_client.request_copyfactory(opts)
        convert_iso_time_to_date(strategy)
        return strategy

    async def update_portfolio_strategy(self, portfolio_id: str, portfolio: CopyFactoryPortfolioStrategyUpdate):
        """Updates a CopyFactory portfolio strategy. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/updatePortfolioStrategy/

        Args:
            portfolio_id: Copy trading portfolio strategy id.
            portfolio: Portfolio strategy update.

        Returns:
            A coroutine resolving when portfolio strategy is updated.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('update_portfolio_strategy')
        payload = deepcopy(portfolio)
        format_request(payload)
        opts = {
            'url': f"/users/current/configuration/portfolio-strategies/{portfolio_id}",
            'method': 'PUT',
            'headers': {'auth-token': self._token},
            'body': payload,
        }
        return await self._domain_client.request_copyfactory(opts)

    async def remove_portfolio_strategy(
        self, portfolio_id: str, close_instructions: CopyFactoryCloseInstructions = None
    ):
        """Deletes a CopyFactory portfolio strategy. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/removePortfolioStrategy/

        Args:
            portfolio_id: Portfolio strategy id.
            close_instructions: Portfolio close instructions.

        Returns:
            A coroutine resolving when portfolio strategy is removed.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('remove_portfolio_strategy')
        opts = {
            'url': f"/users/current/configuration/portfolio-strategies/{portfolio_id}",
            'method': 'DELETE',
            'headers': {'auth-token': self._token},
        }
        if close_instructions is not None:
            format_request(close_instructions)
            opts['body'] = close_instructions
        return await self._domain_client.request_copyfactory(opts)

    async def remove_portfolio_strategy_member(
        self, portfolio_id: str, strategy_id: str, close_instructions: CopyFactoryCloseInstructions = None
    ):
        """Deletes a portfolio strategy member. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/removePortfolioStrategyMember/

        Args:
            portfolio_id: Portfolio strategy id.
            strategy_id: Id of the strategy to delete member for.
            close_instructions: Portfolio close instructions.

        Returns:
            A coroutine resolving when portfolio strategy member is removed.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('remove_portfolio_strategy_member')
        opts = {
            'url': f"/users/current/configuration/portfolio-strategies/{portfolio_id}" f"/members/{strategy_id}",
            'method': 'DELETE',
            'headers': {'auth-token': self._token},
        }
        if close_instructions is not None:
            format_request(close_instructions)
            opts['body'] = close_instructions
        return await self._domain_client.request_copyfactory(opts)

    async def get_subscribers_with_infinite_scroll_pagination(
        self, options: GetSubscribersOptions = None
    ) -> List[CopyFactorySubscriber]:
        """Returns CopyFactory subscribers the user has configured. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/getSubscribers/

        Args:
            options: Options.

        Returns:
            A coroutine resolving with subscribers found.
        """
        return await self._get_subscribers('1', options)

    async def get_subscribers_with_classic_pagination(
        self, options: GetSubscribersOptions = None
    ) -> List[CopyFactorySubscriber]:
        """Returns CopyFactory subscribers the user has configured. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/getSubscribers/

        Args:
            options: Options.

        Returns:
            A coroutine resolving with subscribers found.
        """
        return await self._get_subscribers('2', options)

    async def _get_subscribers(self, api_version: ApiVersion, options: Union[GetSubscribersOptions, None]):
        """Returns CopyFactory subscribers the user has configured. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/getSubscribers/

        Args:
            api_version: API version to use.
            options: Options.

        Returns:
            A coroutine resolving with subscribers found.
        """
        if options is None:
            options = {}
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('get_subscribers')
        opts = {
            'url': f"/users/current/configuration/subscribers",
            'method': 'GET',
            'params': options,
            'headers': {'auth-token': self._token, 'api-version': api_version},
        }
        result = await self._domain_client.request_copyfactory(opts, True)
        convert_iso_time_to_date(result)
        return result

    async def get_subscriber(self, subscriber_id: str) -> CopyFactorySubscriber:
        """Returns CopyFactory subscriber by id. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/getSubscriber/

        Args:
            subscriber_id: Subscriber id.

        Returns:
            A coroutine resolving with subscriber configuration found.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('get_subscriber')
        opts = {
            'url': f"/users/current/configuration/subscribers/{subscriber_id}",
            'method': 'GET',
            'headers': {'auth-token': self._token},
        }
        subscriber = await self._domain_client.request_copyfactory(opts)
        convert_iso_time_to_date(subscriber)
        return subscriber

    async def update_subscriber(self, subscriber_id: str, subscriber: CopyFactorySubscriberUpdate):
        """Updates CopyFactory subscriber configuration. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/updateSubscriber/

        Args:
            subscriber_id: Subscriber id.
            subscriber: Subscriber update.

        Returns:
            A coroutine resolving when subscriber configuration is updated.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('update_subscriber')
        payload = deepcopy(subscriber)
        format_request(payload)
        opts = {
            'url': f"/users/current/configuration/subscribers/{subscriber_id}",
            'method': 'PUT',
            'headers': {'auth-token': self._token},
            'body': payload,
        }
        return await self._domain_client.request_copyfactory(opts)

    async def remove_subscriber(self, subscriber_id: str, close_instructions: CopyFactoryCloseInstructions = None):
        """Deletes subscriber configuration. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/removeSubscriber/

        Args:
            subscriber_id: Subscriber id.
            close_instructions: Subscriber close instructions.

        Returns:
            A coroutine resolving when subscriber configuration is removed.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('remove_subscriber')
        opts = {
            'url': f"/users/current/configuration/subscribers/{subscriber_id}",
            'method': 'DELETE',
            'headers': {'auth-token': self._token},
        }
        if close_instructions is not None:
            format_request(close_instructions)
            opts['body'] = close_instructions
        return await self._domain_client.request_copyfactory(opts)

    async def remove_subscription(
        self, subscriber_id: str, strategy_id: str, close_instructions: CopyFactoryCloseInstructions = None
    ):
        """Deletes a subscription of subscriber to a strategy. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/removeSubscription/

        Args:
            subscriber_id: Subscriber id.
            strategy_id: Strategy id.
            close_instructions: Subscriber close instructions.

        Returns:
            A coroutine resolving when subscription is removed.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('remove_subscription')
        opts = {
            'url': f"/users/current/configuration/subscribers/{subscriber_id}/subscriptions/{strategy_id}",
            'method': 'DELETE',
            'headers': {'auth-token': self._token},
        }
        if close_instructions is not None:
            format_request(close_instructions)
            opts['body'] = close_instructions
        return await self._domain_client.request_copyfactory(opts)

    async def get_webhooks_with_infinite_scroll_pagination(
            self, strategy_id: str, options: GetWebhooksOptions = None
    ) -> List[Webhook]:
        """Retrieves CopyFactory user webhooks list with pagination in infinite scroll style. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/getWebhooks/

        Args:
            strategy_id: Strategy id.
            options: Additional options.

        Returns:
            A coroutine resolving with webhooks found.
        """
        result = await self._get_webhooks(strategy_id, 'infiniteScroll', options)
        for item in result:
            item['createdAt'] = date(item['createdAt'])
        return result

    async def get_webhooks_with_classic_scroll_pagination(
            self, strategy_id: str, options: GetWebhooksOptions = None
    ) -> ClassicPaginationList:
        """Retrieves CopyFactory user webhooks list with pagination in classic style. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/getWebhooks/

        Args:
            strategy_id: Strategy id.
            options: Additional options.

        Returns:
            A coroutine resolving with webhooks found.
        """
        result = await self._get_webhooks(strategy_id, 'classic', options)
        for item in result['items']:
            item['createdAt'] = date(item['createdAt'])
        return result

    async def _get_webhooks(self, strategy_id: str, pagination_style: PaginationStyle,
                            options: Union[GetSubscribersOptions, None]):

        if options is None:
            options = {}
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('get_webhooks')
        opts = {
            'url': f"/users/current/configuration/strategies/{strategy_id}/webhooks",
            'method': 'GET',
            'params': {
                **options,
                'paginationStyle': pagination_style
            },
            'headers': {'auth-token': self._token},
        }
        result = await self._domain_client.request_copyfactory(opts, True)
        return result

    async def create_webhook(self, strategy_id: str, webhook: NewWebhook) -> WebhookIdAndUrl:
        """Creates a new webhook. The webhook can be used for an external app (e.g. TradingView) to submit trading
        signals to CopyFactory. See https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/createWebhook/

        Args:
            strategy_id: Strategy id.
            webhook: Webhook.

        Returns:
            A coroutine resolving with created webhook ID and URL.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('create_webhook')
        payload: dict = deepcopy(webhook)
        format_request(payload)
        opts = {
            'url': f"/users/current/configuration/strategies/{strategy_id}/webhooks",
            'method': 'POST',
            'headers': {'auth-token': self._token},
            'body': payload
        }
        result = await self._domain_client.request_copyfactory(opts)
        return result

    async def update_webhook(self, strategy_id: str, webhook_id: str, update: WebhookUpdate):
        """Updates a webhook. See https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/updateWebhook/

        Args:
            strategy_id: Webhook strategy ID.
            webhook_id: Webhook ID.
            update: Webhook.

        Returns:
            A coroutine resolving when webhook is updated.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('update_webhook')
        payload: dict = deepcopy(update)
        format_request(payload)
        opts = {
            'url': f"/users/current/configuration/strategies/{strategy_id}/webhooks/{webhook_id}",
            'method': 'PATCH',
            'headers': {'auth-token': self._token},
            'body': payload
        }
        await self._domain_client.request_copyfactory(opts)

    async def delete_webhook(self, strategy_id: str, webhook_id: str):
        """Deletes a webhook. See https://metaapi.cloud/docs/copyfactory/restApi/api/configuration/deleteWebhook/

        Args:
            strategy_id: Webhook strategy ID.
            webhook_id: Webhook ID.

        Returns:
            A coroutine resolving when webhook is updated.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('updateWebhook')
        opts = {
            'url': f"/users/current/configuration/strategies/{strategy_id}/webhooks/{webhook_id}",
            'method': 'DELETE',
            'headers': {'auth-token': self._token},
        }
        await self._domain_client.request_copyfactory(opts)
