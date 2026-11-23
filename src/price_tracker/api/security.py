from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request, status


async def require_api_token(
    request: Request,
    x_api_token: str | None = Header(default=None, alias="X-API-Token"),
) -> None:
    """Chroni endpointy API przed nieautoryzowanym dostępem z innych procesów lokalnych / sieci."""
    expected_token: str = request.app.state.api_token
    if not x_api_token or not secrets.compare_digest(x_api_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nieprawidłowy lub brakujący nagłówek X-API-Token.",
        )
