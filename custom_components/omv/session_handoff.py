"""In-memory hand-off of an already-authenticated OMV session across a reload.

The config flow's ``reconfigure``/``reauth``/``user`` steps finish by asking
Home Assistant to (re)load the entry immediately (``async_update_reload_and_abort``
or the initial ``async_create_entry``). Without this hand-off, the following
``async_setup_entry`` call would open a brand new OMV login — and OMV always
challenges a *fresh* ``Session.login`` for two-factor accounts, even when one
was verified moments earlier and even though the same human isn't necessarily
watching for the immediate reload. Stashing the already-authenticated
:class:`~custom_components.omv.omv_api.OMVAPI` instance (and the
``system_info`` already fetched during the flow) lets the reload reuse that
live session instead of failing on a challenge nobody can answer.

Entries are keyed by hostname/unique_id rather than config-entry ID, since a
brand new entry does not have an entry ID yet at the point the flow finishes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .omv_api import OMVAPI

_pending: dict[str, tuple[OMVAPI, dict[str, Any]]] = {}


def store(unique_id: str, api: OMVAPI, system_info: dict[str, Any]) -> None:
    """Stash an authenticated API instance for the next setup of this host."""
    _pending[unique_id] = (api, system_info)


def pop(unique_id: str | None) -> tuple[OMVAPI, dict[str, Any]] | None:
    """Retrieve and remove a previously stashed authenticated API instance, if any."""
    if unique_id is None:
        return None
    return _pending.pop(unique_id, None)


def has_pending(unique_id: str | None) -> bool:
    """Return whether an authenticated API instance is stashed for this host."""
    return unique_id is not None and unique_id in _pending
