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
