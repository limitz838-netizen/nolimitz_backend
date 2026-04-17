from typing import List, Optional

from typing_extensions import TypedDict

from ..domain_client import DomainClient
from ..http_client import HttpClient
from ..metaapi_client import MetaApiClient


class ManifestRoles(TypedDict, total=False):
    """Access rules manifest application roles"""

    description: str
    """Application roles description."""
    roles: List[str]
    """Application roles."""


class ManifestMethod(TypedDict, total=False):
    """Access rules manifest application method group"""

    description: str
    """Method description."""
    method: str
    """Method name."""
    scopes: Optional[List[str]]
    """Method scopes."""


class ManifestMethodGroup(TypedDict, total=False):
    """Access rules manifest application method group"""

    group: str
    """Group name."""
    description: str
    """Method group description."""
    methods: List[ManifestMethod]
    """Method group methods."""


class ManifestService(TypedDict, total=False):
    """Access rules manifest application service"""

    description: str
    """Service description."""
    service: str
    """Service name."""


class ManifestEntity(TypedDict, total=False):
    """Access rules manifest resource entity"""

    description: str
    """Entity description."""
    entity: str
    """Entity name."""
    idDescription: Optional[str]
    """Id description."""


class ManifestAccessRule(TypedDict, total=False):
    """Access rules manifest"""

    id: str
    """Application id."""
    application: str
    """Application name."""
    description: str
    """Application description."""
    entities: List[ManifestEntity]
    """Application resources entities."""
    services: List[ManifestService]
    """Application services."""
    methodGroups: List[ManifestMethodGroup]
    """Application method groups."""
    roles: List[ManifestRoles]
    """Application roles."""
    entityCompositionDescription: Optional[str]
    """Application entity composition description."""


class NarrowedDownToken(TypedDict, total=False):
    """New narrowed down token model"""

    token: str
    """Authorization token value."""


class Method(TypedDict, total=False):
    """Method group method"""

    method: str
    """Method."""
    scopes: Optional[List[str]]
    """Method scopes."""


class MethodGroups(TypedDict, total=False):
    """Narrowed token access rule method groups"""

    group: str
    """Method group."""
    methods: List[Method]
    """Method group methods."""


class AccessRuleResource(TypedDict, total=False):
    """Narrowed token access rule resource"""

    entity: str
    """Entity."""
    id: str
    """Entity id."""


class AccessRule(TypedDict, total=False):
    """Narrowed down token access rule"""

    id: str
    """Application id to grant access to."""
    application: str
    """Application to grant access to."""
    service: str
    """Application service to grant access to."""
    methodGroups: List[MethodGroups]
    """Application service methodGroups to grant access to."""
    resources: List[AccessRuleResource]
    """Application service resources to grant access to."""
    roles: List[str]
    """Access rule roles to grant access to."""


class NarrowDownSimplifiedAccessRules(TypedDict, total=False):
    """Narrowed down token simplified access rules"""

    resources: Optional[List[AccessRuleResource]]
    """Resources to grant access to."""
    roles: Optional[List[str]]
    """Roles to grant access to."""
    applications: Optional[List[str]]
    """Applications to grant access to."""


class NarrowDownAccessRules(TypedDict, total=False):
    """Narrowed down token access rules"""

    accessRules: List[AccessRule]
    """applications access rules to grant."""


class TokenManagementClient(MetaApiClient):
    """metaapi.cloud Token Management API client"""

    def __init__(self, http_client: HttpClient, domain_client: DomainClient):
        """Initializes token management API client instance.

        Args:
            http_client: HTTP client.
            domain_client: Domain client.
        """
        super().__init__(http_client, domain_client)
        self._host = f'https://profile-api-v1.{domain_client.domain}'

    async def get_access_rules(self) -> List[ManifestAccessRule]:
        """Gets access rules manifest

        Returns:
            A coroutine resolving with List[ManifestAccessRule] - Access rules manifest.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('get_access_rules')
        opts = {'url': f'{self._host}/access-rule-manifest', 'method': 'GET', 'headers': {'auth-token': self._token}}
        return await self._http_client.request(opts, 'get_access_rules')

    async def narrow_down_token(
        self,
        narrow_down_payload: NarrowDownAccessRules or NarrowDownSimplifiedAccessRules,
        validity_in_hours: float = None,
    ) -> NarrowedDownToken:
        """Returns narrowed down token with given access rules

        Args:
            narrow_down_payload: Narrow down payload.
            validity_in_hours: Token validity in hours.

        Returns:
            A coroutine resolving with NarrowDownToken - Narrowed down token.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('narrow_down_token')
        if validity_in_hours:
            validity_in_hours = '?validity-in-hours=' + str(validity_in_hours)
        opts = {
            'url': f'{self._host}/users/current/narrow-down-auth-token{validity_in_hours or ""}',
            'method': 'POST',
            'headers': {'auth-token': self._token},
            'body': narrow_down_payload,
        }
        return await self._http_client.request(opts, 'narrow_down_token')
