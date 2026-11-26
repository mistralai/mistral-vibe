"""Session API contract: request, response, state and event models.

The modules below this package are unchanged copies of the shared Session API
definitions. They keep their original layout so their relative imports resolve.
They are refreshed as a whole from the Session API definitions they copy and
must never be edited in this package.

Only ``app_server/session/__init__.py`` is written for this package: the source
version also exports the Session client, which this Runtime does not use.
"""

COPIED_FILES = (
    "session_values.py",
    "agent_messages.py",
    "app_server/__init__.py",
    "app_server/session/transport.py",
    "app_server/session/models/__init__.py",
    "app_server/session/models/base.py",
    "app_server/session/models/callbacks.py",
    "app_server/session/models/common.py",
    "app_server/session/models/configuration.py",
    "app_server/session/models/content.py",
    "app_server/session/models/events.py",
    "app_server/session/models/history.py",
    "app_server/session/models/state.py",
    "app_server/session/procedures/__init__.py",
    "app_server/session/procedures/session.py",
)
"""Paths, relative to the source package, of the files copied unchanged."""
