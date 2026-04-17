import pytest
from mock import MagicMock, AsyncMock

from .trading_client import TradingClient
from ...models import date

copy_factory_api_url = 'https://copyfactory-application-history-master-v1.agiliumtrade.agiliumtrade.ai'
domain_client = MagicMock()
configuration_client = MagicMock()
trading_client = TradingClient(domain_client, configuration_client)
token = 'header.payload.sign'


@pytest.fixture(autouse=True)
async def run_around_tests():
    global domain_client
    domain_client = MagicMock()
    domain_client.request_copyfactory = AsyncMock()
    domain_client.token = token
    global configuration_client
    configuration_client = MagicMock()
    configuration_client.get_strategy = AsyncMock()
    global trading_client
    trading_client = TradingClient(domain_client, configuration_client)


class TestTradingClient:
    @pytest.mark.asyncio
    async def test_resynchronize_copyfactory_account(self):
        """Should resynchronize CopyFactory account."""
        await trading_client.resynchronize('e8867baa-5ec2-45ae-9930-4d5cea18d0d6', ['ABCD'], ['0123456'])
        domain_client.request_copyfactory.assert_called_with(
            {
                'url': '/users/current/subscribers/e8867baa-5ec2-45ae-9930-4d5cea18d0d6/resynchronize',
                'method': 'POST',
                'headers': {'auth-token': token},
                'params': {'strategyId': ['ABCD'], 'positionId': ['0123456']},
            }
        )

    @pytest.mark.asyncio
    async def test_not_resynchronize_account_with_account_token(self):
        """Should not resynchronize CopyFactory subscriber with account token."""
        domain_client.token = 'token'
        trading_client = TradingClient(domain_client, configuration_client)
        try:
            await trading_client.resynchronize('e8867baa-5ec2-45ae-9930-4d5cea18d0d6', ['ABCD'], ['0123456'])
            pytest.fail()
        except Exception as err:
            assert (
                err.__str__()
                == 'You can not invoke resynchronize method, '
                + 'because you have connected with account access token. Please use API access token from '
                + 'https://app.metaapi.cloud/token page to invoke this method.'
            )

    @pytest.mark.asyncio
    async def test_retrieve_stopouts(self):
        """Should retrieve stopouts."""
        expected = [
            {
                'strategyId': 'accountId',
                'reason': 'monthly-balance',
                'stoppedAt': '2020-08-08T07:57:30.328Z',
                'strategy': {'id': 'ABCD', 'name': 'Strategy'},
                'reasonDescription': 'total strategy equity drawdown exceeded limit',
                'sequenceNumber': 2,
            }
        ]
        domain_client.request_copyfactory = AsyncMock(return_value=expected)
        stopouts = await trading_client.get_stopouts('e8867baa-5ec2-45ae-9930-4d5cea18d0d6')
        assert stopouts == expected
        domain_client.request_copyfactory.assert_called_with(
            {
                'url': '/users/current/subscribers/e8867baa-5ec2-45ae-9930-4d5cea18d0d6/stopouts',
                'method': 'GET',
                'headers': {'auth-token': token},
            }
        )

    @pytest.mark.asyncio
    async def test_not_retrieve_stopouts_with_account_token(self):
        """Should not retrieve stopouts from API with account token."""
        domain_client.token = 'token'
        trading_client = TradingClient(domain_client, configuration_client)
        try:
            await trading_client.get_stopouts('e8867baa-5ec2-45ae-9930-4d5cea18d0d6')
            pytest.fail()
        except Exception as err:
            assert (
                err.__str__()
                == 'You can not invoke get_stopouts method, '
                + 'because you have connected with account access token. Please use API access token from '
                + 'https://app.metaapi.cloud/token page to invoke this method.'
            )

    @pytest.mark.asyncio
    async def test_reset_stopouts(self):
        """Should reset stopouts."""
        await trading_client.reset_subscription_stopouts('e8867baa-5ec2-45ae-9930-4d5cea18d0d6', 'ABCD', 'daily-equity')
        domain_client.request_copyfactory.assert_called_with(
            {
                'url': '/users/current/subscribers/'
                + 'e8867baa-5ec2-45ae-9930-4d5cea18d0d6/subscription-strategies/ABCD/stopouts/daily-equity/reset',
                'method': 'POST',
                'headers': {'auth-token': token},
            }
        )

    @pytest.mark.asyncio
    async def test_not_reset_stopouts_with_account_token(self):
        """Should not reset stopouts with account token."""
        domain_client.token = 'token'
        trading_client = TradingClient(domain_client, configuration_client)
        try:
            await trading_client.reset_subscription_stopouts(
                'e8867baa-5ec2-45ae-9930-4d5cea18d0d6', 'ABCD', 'daily-equity'
            )
            pytest.fail()
        except Exception as err:
            assert (
                err.__str__()
                == 'You can not invoke reset_subscription_stopouts method, '
                + 'because you have connected with account access token. Please use API access token from '
                + 'https://app.metaapi.cloud/token page to invoke this method.'
            )

    @pytest.mark.asyncio
    async def test_reset_subscriber_stopouts(self):
        """Should reset subscriber stopouts."""
        await trading_client.reset_subscriber_stopouts('e8867baa-5ec2-45ae-9930-4d5cea18d0d6', 'daily-equity')
        domain_client.request_copyfactory.assert_called_with(
            {
                'url': '/users/current/subscribers/e8867baa-5ec2-45ae-9930-4d5cea18d0d6/stopouts/daily-equity/reset',
                'method': 'POST',
                'headers': {'auth-token': token},
            }
        )

    @pytest.mark.asyncio
    async def test_not_reset_subscriber_stopouts_with_account_token(self):
        """Should not reset subscriber stopouts with account token."""
        domain_client.token = 'token'
        trading_client = TradingClient(domain_client, configuration_client)
        try:
            await trading_client.reset_subscriber_stopouts('e8867baa-5ec2-45ae-9930-4d5cea18d0d6', 'daily-equity')
            pytest.fail()
        except Exception as err:
            assert (
                err.__str__()
                == 'You can not invoke reset_subscriber_stopouts method, '
                + 'because you have connected with account access token. Please use API access token from '
                + 'https://app.metaapi.cloud/token page to invoke this method.'
            )

    @pytest.mark.asyncio
    async def test_retrieve_copy_trading_log(self):
        """Should retrieve copy trading user log."""
        expected = [{'time': '2020-08-08T07:57:30.328Z', 'level': 'INFO', 'message': 'message'}]
        domain_client.request_copyfactory = AsyncMock(return_value=expected)
        records = await trading_client.get_user_log(
            'e8867baa-5ec2-45ae-9930-4d5cea18d0d6',
            date('2020-08-01T00:00:00.000Z'),
            date('2020-08-10T00:00:00.000Z'),
            'strategyId',
            'positionId',
        )
        assert records == expected
        domain_client.request_copyfactory.assert_called_with(
            {
                'url': '/users/current/subscribers/e8867baa-5ec2-45ae-9930-4d5cea18d0d6/user-log',
                'method': 'GET',
                'params': {
                    'startTime': '2020-08-01T00:00:00.000Z',
                    'endTime': '2020-08-10T00:00:00.000Z',
                    'offset': 0,
                    'limit': 1000,
                    'strategyId': 'strategyId',
                    'positionId': 'positionId',
                },
                'headers': {'auth-token': token},
            },
            True,
        )

    @pytest.mark.asyncio
    async def test_not_retrieve_copy_trading_log_with_account_token(self):
        """Should not retrieve copy trading user log from API with account token."""
        domain_client.token = 'token'
        trading_client = TradingClient(domain_client, configuration_client)
        try:
            await trading_client.get_user_log('e8867baa-5ec2-45ae-9930-4d5cea18d0d6')
            pytest.fail()
        except Exception as err:
            assert (
                err.__str__()
                == 'You can not invoke get_user_log method, '
                + 'because you have connected with account access token. Please use API access token from '
                + 'https://app.metaapi.cloud/token page to invoke this method.'
            )

    @pytest.mark.asyncio
    async def test_retrieve_copy_trading_strategy_log(self):
        """Should retrieve copy trading strategy log."""
        expected = [{'time': '2020-08-08T07:57:30.328Z', 'level': 'INFO', 'message': 'message'}]
        domain_client.request_copyfactory = AsyncMock(return_value=expected)
        records = await trading_client.get_strategy_log(
            'ABCD', date('2020-08-01T00:00:00.000Z'), date('2020-08-10T00:00:00.000Z'), 'positionId', 'DEBUG'
        )
        assert records == expected
        domain_client.request_copyfactory.assert_called_with(
            {
                'url': '/users/current/strategies/ABCD/user-log',
                'method': 'GET',
                'params': {
                    'startTime': '2020-08-01T00:00:00.000Z',
                    'endTime': '2020-08-10T00:00:00.000Z',
                    'offset': 0,
                    'limit': 1000,
                    'level': 'DEBUG',
                    'positionId': 'positionId',
                },
                'headers': {'auth-token': token},
            },
            True,
        )

    @pytest.mark.asyncio
    async def test_not_retrieve_copy_trading_strategy_log_with_account_token(self):
        """Should not retrieve copy trading strategy log from API with account token."""
        domain_client.token = 'token'
        trading_client = TradingClient(domain_client, configuration_client)
        try:
            await trading_client.get_strategy_log('ABCD')
            pytest.fail()
        except Exception as err:
            assert (
                err.__str__()
                == 'You can not invoke get_strategy_log method, '
                + 'because you have connected with account access token. Please use API access token from '
                + 'https://app.metaapi.cloud/token page to invoke this method.'
            )

    @pytest.mark.asyncio
    async def test_get_subscriber_signal_client(self):
        """Should get subscriber signal client."""
        domain_client.get_account_info = AsyncMock(return_value={'id': 'accountId', 'regions': ['vint-hill']})

        async def get_signal_client_host(regions):
            return {'host': 'https://copyfactory-api-v1', 'regions': regions, 'domain': 'agiliumtrade.ai'}

        domain_client.get_signal_client_host = AsyncMock(side_effect=get_signal_client_host)
        client = await trading_client.get_subscriber_signal_client('accountId')
        assert client._account_id == 'accountId'
        assert client._host['regions'] == ['vint-hill']

    @pytest.mark.asyncio
    async def test_get_strategy_signal_client(self):
        """Should get strategy signal client."""
        domain_client.get_account_info = AsyncMock(return_value={'id': 'accountId', 'regions': ['vint-hill']})
        strategy_info = {
            '_id': 'ABCD',
            'platformCommissionRate': 0.01,
            'name': 'Test strategy',
            'accountId': 'e8867baa-5ec2-45ae-9930-4d5cea18d0d6',
            'maxTradeRisk': 0.1,
            'riskLimits': [
                {
                    'type': 'monthly',
                    'applyTo': 'balance',
                    'maxRelativeRisk': 0.5,
                    'closePositions': False,
                    'startTime': '2020-08-24T00:00:01.000Z',
                }
            ],
            'timeSettings': {'lifetimeInHours': 192, 'openingIntervalInMinutes': 5},
        }
        configuration_client.get_strategy = AsyncMock(return_value=strategy_info)

        async def get_signal_client_host(regions):
            return {'host': 'https://copyfactory-api-v1', 'regions': regions, 'domain': 'agiliumtrade.ai'}

        domain_client.get_signal_client_host = AsyncMock(side_effect=get_signal_client_host)
        client = await trading_client.get_strategy_signal_client('ABCD')
        assert client._account_id == 'accountId'
        assert client._host['regions'] == ['vint-hill']

    @pytest.mark.asyncio
    async def test_add_stopout_listener(self):
        """Should add stopout listener."""
        call_stub = MagicMock(return_value='listenerId')
        trading_client._stopout_listener_manager.add_stopout_listener = call_stub
        listener = MagicMock()
        listener_id = trading_client.add_stopout_listener(listener, 'accountId', 'ABCD', 1)
        assert listener_id == 'listenerId'
        call_stub.assert_called_with(listener, 'accountId', 'ABCD', 1)

    @pytest.mark.asyncio
    async def test_remove_stopout_listener(self):
        """Should remove stopout listener."""
        call_stub = MagicMock()
        trading_client._stopout_listener_manager.remove_stopout_listener = call_stub
        trading_client.remove_stopout_listener('id')
        call_stub.assert_called_with('id')


class TestUserLogListener:
    @pytest.mark.asyncio
    async def test_add_strategy_log_listener(self):
        """Should add strategy listener."""
        call_stub = MagicMock(return_value='listenerId')
        trading_client._user_log_listener_manager.add_strategy_log_listener = call_stub
        listener = MagicMock()
        listener_id = trading_client.add_strategy_log_listener(listener, 'ABCD')
        assert listener_id == 'listenerId'
        call_stub.assert_called_with(listener, 'ABCD', None, None, None, None)

    @pytest.mark.asyncio
    async def test_remove_strategy_log_listener(self):
        """Should remove stopout listener."""
        call_stub = MagicMock()
        trading_client._stopout_listener_manager.remove_stopout_listener = call_stub
        trading_client.remove_stopout_listener('id')
        call_stub.assert_called_with('id')

    @pytest.mark.asyncio
    async def test_add_subscriber_log_listener(self):
        """Should add subscriber listener."""
        call_stub = MagicMock(return_value='listenerId')
        trading_client._user_log_listener_manager.add_subscriber_log_listener = call_stub
        listener = MagicMock()
        listener_id = trading_client.add_subscriber_log_listener(listener, 'accountId')
        assert listener_id == 'listenerId'
        call_stub.assert_called_with(listener, 'accountId', None, None, None, None, None)

    @pytest.mark.asyncio
    async def test_remove_subscriber_log_listener(self):
        """Should remove subscriber listener."""
        call_stub = MagicMock()
        trading_client._user_log_listener_manager.remove_subscriber_log_listener = call_stub
        trading_client.remove_subscriber_log_listener('id')
        call_stub.assert_called_with('id')
