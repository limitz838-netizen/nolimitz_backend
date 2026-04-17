import pytest
from mock import MagicMock, AsyncMock

from .subscriber_signal_client import SubscriberSignalClient

domain_client = MagicMock()
token = 'header.payload.sign'
host = {'host': 'https://copyfactory-api-v1', 'region': 'vint-hill', 'domain': 'agiliumtrade.ai'}
signal_client = SubscriberSignalClient('accountId', host, domain_client)


@pytest.fixture(autouse=True)
async def run_around_tests():
    global domain_client
    domain_client = MagicMock()
    domain_client.token = token
    domain_client.request_signal = AsyncMock()
    global signal_client
    signal_client = SubscriberSignalClient('accountId', host, domain_client)


class TestTradingClient:
    @pytest.mark.asyncio
    async def test_retrieve_signals(self):
        """Should retrieve signals."""
        expected = [
            {
                'symbol': 'EURUSD',
                'type': 'POSITION_TYPE_BUY',
                'time': '2020-08-24T00:00:00.000Z',
                'closeAfter': '2020-08-24T00:00:00.000Z',
                'volume': 1,
            }
        ]

        domain_client.request_signal = AsyncMock(return_value=expected)
        stopouts = await signal_client.get_trading_signals()
        assert stopouts == expected
        domain_client.request_signal.assert_called_with(
            {'url': '/users/current/subscribers/accountId/signals', 'method': 'GET', 'headers': {'auth-token': token}},
            host,
            'accountId',
        )
