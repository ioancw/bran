"""UI auth stub.

V1: no auth — every request is the same anonymous user. Kept as a single
dependency so the seam for OAuth / SAML / per-user auth is one file to swap.

When you're ready to add real auth, replace `get_current_user()` with a
function that resolves the user from a session cookie or JWT and returns
their identity (or raises 401). All UI routes already depend on it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WebUser:
    id: str
    name: str
    is_admin: bool = True  # only user today is the operator


_DEFAULT = WebUser(id="anonymous", name="anonymous", is_admin=True)


def get_current_user() -> WebUser:
    """FastAPI dependency — returns the active user for a UI request.

    Today this always returns the placeholder anonymous user, with admin
    privileges (since the only user IS the operator). Swap when adding auth.
    """
    return _DEFAULT
