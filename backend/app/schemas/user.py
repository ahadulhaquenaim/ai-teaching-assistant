"""User schema used by `get_current_user`.

Auth is stubbed out for now (see `core/dependencies.py`): every request is
treated as the same local dev user. Endpoints and repositories still take a
`user_id` and perform ownership checks, so swapping in real Google auth later
only means replacing `get_current_user`'s implementation.
"""

from __future__ import annotations

from pydantic import BaseModel


class CurrentUser(BaseModel):
    """The authenticated principal for the current request."""

    user_id: str
    email: str
    name: str
