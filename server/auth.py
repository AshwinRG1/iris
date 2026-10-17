"""The shared API-key check. Routes depend on one of these; none compare keys themselves."""

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query

from deps import Deps, get_deps


def key_matches(configured: str, supplied: str | None) -> bool:
    """True when no key is configured, else a constant-time comparison."""
    if not configured:
        return True
    if supplied is None:
        return False
    return hmac.compare_digest(configured.encode(), supplied.encode())


def _check(configured: str, supplied: str | None) -> None:
    if not key_matches(configured, supplied):
        raise HTTPException(status_code=401, detail="missing or wrong key")


def require_key_header(
    deps: Annotated[Deps, Depends(get_deps)],
    x_iris_key: Annotated[str | None, Header()] = None,
) -> None:
    """For API routes (/frame, PUT /tuning): the `X-Iris-Key` header."""
    _check(deps.settings.iris_api_key, x_iris_key)


def require_key_header_or_query(
    deps: Annotated[Deps, Depends(get_deps)],
    x_iris_key: Annotated[str | None, Header()] = None,
    key: Annotated[str | None, Query()] = None,
) -> None:
    """For the live view (browser links cannot set headers): the header or `?key=`."""
    _check(deps.settings.iris_api_key, x_iris_key if x_iris_key is not None else key)
