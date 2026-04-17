import asyncio
import json
import math
import sys
from datetime import datetime
from typing import Optional

import httpx
import pytz
from httpx import HTTPError, Response
from typing_extensions import TypedDict

from .error_handler import (
    UnauthorizedException,
    ForbiddenException,
    ApiException,
    ConflictException,
    ValidationException,
    InternalException,
    NotFoundException,
    TooManyRequestsException,
)
from .options_validator import OptionsValidator
from .timeout_exception import TimeoutException
from ..logger import LoggerManager
from ..metaapi.models import ExceptionMessage, date, format_error

if sys.version_info[0] == 3 and sys.version_info[1] >= 8 and sys.platform.startswith('win'):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class RequestOptions(TypedDict, total=False):
    """Options for HttpClient requests."""

    method: Optional[str]
    url: str
    headers: Optional[dict]
    params: Optional[dict]
    body: Optional[dict]
    files: Optional[dict]


class HttpClient:
    """HTTP client library based on requests module."""

    def __init__(self, timeout: float = None, retry_opts=None):
        """Initializes HttpClient class instance.

        Args:
            timeout: Request timeout in seconds.
        """
        validator = OptionsValidator()
        timeout = timeout or 60
        if retry_opts is None:
            retry_opts = {}
        self._timeout = timeout
        self._retries = validator.validate_number(retry_opts.get('retries'), 5, 'retry_opts.retries')
        self._min_retry_delay_in_seconds = validator.validate_non_zero(
            retry_opts.get('minDelayInSeconds'), 1, 'retry_opts.minDelayInSeconds'
        )
        self._max_retry_delay_in_seconds = validator.validate_non_zero(
            retry_opts.get('maxDelayInSeconds'), 30, 'retry_opts.maxDelayInSeconds'
        )
        self._long_running_request_timeout = validator.validate_number(
            retry_opts.get('longRunningRequestTimeoutInMinutes'), 10, 'retry_opts.longRunningRequestTimeoutInMinutes'
        )
        self._logger = LoggerManager.get_logger('HttpClient')

    async def request(
        self,
        options: RequestOptions,
        type: str = '',
        retry_counter: int = 0,
        end_time: float = None,
        isLongRunning: bool = False,
    ) -> Response:
        """Performs a request. Response errors are returned as ApiException or subclasses.

        Args:
            options: Request options.

        Returns:
            A request response.
        """
        if not end_time:
            end_time = datetime.now().timestamp() + self._max_retry_delay_in_seconds * self._retries
        retry_after_seconds = 0
        try:
            response = await self._make_request(options)
            response.raise_for_status()
            if response.status_code == 202:
                retry_after_seconds = response.headers['retry-after']
                if isinstance(retry_after_seconds, str):
                    try:
                        retry_after_seconds = float(retry_after_seconds)
                    except Exception:
                        retry_after_seconds = (
                            datetime.strptime(retry_after_seconds, '%a, %d %b %Y %H:%M:%S GMT')
                            .replace(tzinfo=pytz.UTC)
                            .timestamp()
                            - datetime.now().timestamp()
                        )
                if isLongRunning is False:
                    end_time = datetime.now().timestamp() + self._long_running_request_timeout * 60
                    isLongRunning = True
            if response.content:
                try:
                    response = response.json()
                except Exception as err:
                    print('Error parsing json', format_error(err))
        except HTTPError as err:
            retry_counter = await self._handle_error(err, type, retry_counter, end_time)
            return await self.request(options, type, retry_counter, end_time)
        if retry_after_seconds:
            if isinstance(response, dict) and 'message' in response:
                self._logger.info(
                    f'Retrying request in {math.floor(retry_after_seconds)} seconds because request '
                    'returned message:',
                    response['message'],
                )
            await self._handle_retry(end_time, retry_after_seconds)
            response = await self.request(options, type, retry_counter, end_time, isLongRunning)
        return response

    async def _make_request(self, options: RequestOptions) -> Response:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            method = options.get('method', 'GET')
            url = options['url']
            params = options.get('params')
            files = options.get('files')
            headers = options.get('headers')
            body = options.get('body')
            req = client.build_request(method, url, params=params, files=files, headers=headers, json=body)
            response = await client.send(req)
            return response

    async def _handle_retry(self, end_time: float, retry_after: float):
        if end_time > datetime.now().timestamp() + retry_after:
            await asyncio.sleep(retry_after)
        else:
            raise TimeoutException('Timed out waiting for the response')

    async def _handle_error(self, err, type: str, retry_counter: int, end_time: float):
        if err.__class__.__name__ == 'ConnectTimeout':
            error = err
        else:
            error = self._convert_error(err)
        if (
            error.__class__.__name__ in ['ConflictException', 'InternalException', 'ApiException', 'ConnectTimeout']
            and retry_counter < self._retries
        ):
            pause = min(pow(2, retry_counter) * self._min_retry_delay_in_seconds, self._max_retry_delay_in_seconds)
            await asyncio.sleep(pause)
            return retry_counter + 1
        elif error.__class__.__name__ == 'TooManyRequestsException':
            retry_time = date(error.metadata['recommendedRetryTime']).timestamp()
            if retry_time < end_time:
                self._logger.debug(
                    f'{type} request has failed with TooManyRequestsError (HTTP status code 429). '
                    + f'Will retry request in '
                    + f'{math.ceil((retry_time - datetime.now().timestamp()) / 1000)} seconds'
                )
                await asyncio.sleep(retry_time - datetime.now().timestamp())
                return retry_counter
        raise error

    def _convert_error(self, err: HTTPError):
        try:
            response: ExceptionMessage or TypedDict = json.loads(err.response.text)
        except Exception:
            response = {}
        err_message = (
            response['message']
            if 'message' in response
            else (err.response.reason_phrase if hasattr(err, 'response') else None)
        )
        url = err.request.url
        status = None
        if hasattr(err, 'response'):
            status = err.response.status_code
        if status == 400:
            details = response.get('details', [])
            return ValidationException(err_message, details, url)
        elif status == 401:
            return UnauthorizedException(err_message, url)
        elif status == 403:
            return ForbiddenException(err_message, url)
        elif status == 404:
            return NotFoundException(err_message, url)
        elif status == 409:
            return ConflictException(err_message, url)
        elif status == 429:
            err_metadata = response.get('metadata', {})
            return TooManyRequestsException(err_message, err_metadata, url)
        elif status == 500:
            return InternalException(err_message, url)
        else:
            return ApiException(err_message, status, url)
