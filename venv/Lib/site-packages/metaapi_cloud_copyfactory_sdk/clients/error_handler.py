from typing import Tuple, List

from typing_extensions import TypedDict


class ApiException(Exception):
    """Base class for API exceptions. Contains indication of HTTP status.

    Attributes:
        status_code: HTTP status code.
        url: API request URL.
    """

    def __init__(self, message: str, status: int, url: str = None):
        """Initializes ApiException.

        Args:
            message: Exception message.
            status: HTTP status.
            url: API request URL.
        """
        super().__init__(f'{message}. Request URL: {url}' if url else message)
        self.status_code = status
        self.url = url

    @property
    def code(self) -> str:
        """Returns exception code used for i18n

        Returns:
            Exception code.
        """
        return self._code

    @code.setter
    def code(self, code: str):
        """Sets error code, used for i18n

        Args:
            code: Error code for i18n.
        """
        self._code = code

    @property
    def arguments(self) -> Tuple:
        """Returns message arguments for i18n

        Returns:
            Message arguments for i18n.
        """
        return self.args

    @arguments.setter
    def arguments(self, args: Tuple):
        """Set message arguments for i18n

        Args:
            args: Arguments for i18n.
        """
        self.args = args


class NotFoundException(ApiException):
    """Throwing this exception results in 404 (Not Found) HTTP response code."""

    def __init__(self, message: str, url: str = None):
        """Initializes not found exception.

        Args:
            message: Exception message.
            url: API request URL.
        """
        super().__init__(message, 404, url)


class ForbiddenException(ApiException):
    """Throwing this exception results in 403 (Forbidden) HTTP response code."""

    def __init__(self, message: str, url: str = None):
        """Initializes forbidden exception.

        Args:
            message: Exception message.
            url: API request URL.
        """
        super().__init__(message, 403, url)


class UnauthorizedException(ApiException):
    """Throwing this exception results in 401 (Unauthorized) HTTP response code."""

    def __init__(self, message: str, url: str = None):
        """Initializes unauthorized exception.

        Args:
            message: Exception message.
            url: API request URL.
        """
        super().__init__(message, 401, url)


class ValidationException(ApiException):
    """Represents validation exception. Throwing this exception results in 400 (Bad Request) HTTP response code.

    Attributes:
        _details: Validation exception details
    """

    def __init__(self, message: str, details: List[dict] = None, url: str = None):
        """Initializes validation error.

        Args:
            message: Exception message.
            details: Exception data.
            url: API request URL.
        """
        super().__init__(message + ', check error.details for more information', 400, url)
        self._details = details

    @property
    def details(self) -> List[dict]:
        """Returns validation exception details.

        Returns:
            Validation exception details.
        """
        return self._details


class InternalException(ApiException):
    """Represents unexpected exception. Throwing this error results in 500 (Internal Error) HTTP response code."""

    def __init__(self, message: str, url: str = None):
        """Initializes unexpected exception.

        Args:
            message: Exception message.
            url: API request URL.
        """
        super().__init__(message, 500, url)


class ConflictException(ApiException):
    """Represents conflict exception. Throwing this exception results in 409 (Conflict) HTTP response code."""

    def __init__(self, message: str, url: str = None):
        """Initializes conflict exception.

        Args:
            message: Exception message.
            url: API request URL.
        """
        super().__init__(message, 409, url)


class TooManyRequestsErrorMetadata(TypedDict):
    """Metadata for too many requests error."""

    periodInMinutes: float
    """Throttling period in minutes."""
    requestsPerPeriodAllowed: int
    """Available requests for periodInMinutes."""
    recommendedRetryTime: str
    """Recommended date to retry request."""


class TooManyRequestsException(ApiException):
    """Represents too many requests error. Throwing this exception results in 429 (Too Many Requests) HTTP response
    code."""

    def __init__(self, message: str, metadata: TooManyRequestsErrorMetadata, url: str):
        """Initializes too many requests exception.

        Args:
            message: Exception message.
            metadata: Error metadata.
            url: API request URL.
        """
        super().__init__(message, 429, url)
        self.metadata = metadata
