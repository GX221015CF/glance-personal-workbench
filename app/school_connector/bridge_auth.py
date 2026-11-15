"""Separate, narrowly scoped same-user authentication for native clients.

Origin is deliberately absent. This is not proof of a particular executable:
other programs running as this Windows user are inside the trust boundary.
"""
import json
import os
from pathlib import Path
import secrets
import re

from .crypto import protect

SCOPES = {
    ('POST', '/api/desktop-relay/v1/control'),
    ('POST', '/api/school-connector/v1/check/sufe'),
    ('POST', '/api/school-connector/v1/check/canvas'),
    ('POST', '/api/school-connector/v1/check/academic'),
    ('GET', '/api/sufe/v1/status'), ('POST', '/api/sufe/v1/connect'),
    ('GET', '/api/canvas/v1/status'), ('POST', '/api/canvas/v1/connect'),
    ('GET', '/api/academic/v1/status'), ('POST', '/api/academic/v1/connect'),
    ('POST', '/api/academic/v1/verify'),
}
HEADER = 'X-School-Connector-Token'


def auth_path():
    # AppData may resolve to an application's private LocalCache even for a
    # same-user child with no package identity. Browser-launched hosts need the
    # same one per-process credential as the existing bridge, outside that view.
    from workbench_config import DATA_DIR
    return DATA_DIR / 'SchoolConnector' / 'bridge-auth.dpapi'


class NativeBridgeAuth:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else auth_path()
        self._token = secrets.token_urlsafe(32)
        self.ready = False

    def publish(self):
        """Called only after the server owns port 8182, not when modules import."""
        self.ready = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + secrets.token_hex(8) + '.tmp')
        payload = protect(json.dumps({'version': 1, 'token': self._token, 'pid': os.getpid()}).encode())
        try:
            with temporary.open('xb') as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
            self.ready = True
        finally:
            temporary.unlink(missing_ok=True)

    def authorize(self, handler):
        token = handler.headers.get(HEADER, '')
        return (self.ready and handler.local_request()
                and handler.headers.get('Origin') is None
                and (handler.command, handler.path) in SCOPES
                and isinstance(token, str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', token) is not None
                and secrets.compare_digest(token, self._token))
