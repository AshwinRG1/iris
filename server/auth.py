"""The shared API-key check. Routes depend on one of these; none compare keys themselves."""

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from deps import Deps, get_deps


def key_matches(configured: str, supplied: str | None) -> bool:
    """True when no key is configured, else a constant-time comparison."""
    if not configured:
        return True
    if supplied is None:
        return False
    return hmac.compare_digest(configured.encode(), supplied.encode())


def require_key_header(
    deps: Annotated[Deps, Depends(get_deps)],
    x_iris_key: Annotated[str | None, Header()] = None,
) -> None:
    """For API routes (/frame, PUT /tuning): the `X-Iris-Key` header."""
    if not key_matches(deps.settings.iris_api_key, x_iris_key):
        raise HTTPException(status_code=401, detail="missing or wrong key")


def require_key_header_or_query(
    deps: Annotated[Deps, Depends(get_deps)],
    x_iris_key: Annotated[str | None, Header()] = None,
    key: str | None = None,
) -> None:
    """For the live view (a browser can't set headers on an <img> or a typed URL).

    A non-empty `X-Iris-Key` header wins; an empty or absent one falls back to `?key=`.
    """
    if not key_matches(deps.settings.iris_api_key, x_iris_key or key):
        raise HTTPException(status_code=401, detail="missing or wrong key")
