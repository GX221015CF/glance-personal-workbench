"""Fixed v1 endpoints; no browser Origin and no caller-controlled URLs or paths."""
import json
import re
import socket
import urllib.error
import urllib.request

from .bridge_auth import auth_path, HEADER
from .crypto import protect
from .protocol import ConnectorError

BASE = 'http://127.0.0.1:8182'
PATHS = {'sufe': '/api/sufe/v1', 'canvas': '/api/canvas/v1', 'academic': '/api/academic/v1'}


def safe_meta(meta):
    if not isinstance(meta, dict):
        raise ConnectorError('BRIDGE_RESPONSE_INVALID')
    result = {}
    for key in ('available', 'stale', 'needsLogin', 'refreshing'):
        if type(meta.get(key)) is not bool:
            raise ConnectorError('BRIDGE_RESPONSE_INVALID')
        result[key] = meta[key]
    for key in ('updatedAt', 'checkedAt'):
        value = meta.get(key)
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T[0-9:+.-]{8,30}', value)):
            raise ConnectorError('BRIDGE_RESPONSE_INVALID')
        result[key] = value
    result['errorCode'] = meta.get('errorCode') if meta.get('errorCode') in ('LOGIN_REQUIRED', 'INVALID_SESSION', 'UPSTREAM_ERROR', 'SYNC_FAILED', 'PLAN_CONFLICT') else None
    overview = meta.get('creditOverview')
    if isinstance(overview, dict):
        stamp = overview.get('updatedAt')
        result['creditOverview'] = {
            'available': overview.get('available') is True, 'stale': overview.get('stale') is True,
            'updatedAt': stamp if isinstance(stamp, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T[0-9:+.-]{8,30}', stamp) else None,
            'errorCode': overview.get('errorCode') if overview.get('errorCode') in (
                'NOT_ACQUIRED', 'PAGE_UNAVAILABLE', 'SOURCE_CHANGED', 'INVALID_FIELDS') else None}
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


class BridgeClient:
    def __init__(self, credential_path=None, opener=None):
        self.credential_path = credential_path if credential_path is not None else auth_path()
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def _token(self):
        try:
            if self.credential_path.stat().st_size > 8192:
                raise ValueError()
            data = json.loads(protect(self.credential_path.read_bytes(), decrypt=True))
            if data.get('version') != 1 or not isinstance(data.get('token'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', data['token']):
                raise ValueError()
            return data['token']
        except Exception:
            raise ConnectorError('NATIVE_AUTH_UNAVAILABLE', True) from None

    def request(self, target, command, session=None):
        path = '/api/school-connector/v1/check/' + target if command == 'check' else PATHS[target] + '/' + command
        body = None if command == 'status' else b'' if command == 'check' else protect(json.dumps(session, ensure_ascii=False).encode('utf-8'))
        for attempt in range(2):
            headers = {HEADER: self._token()}
            if body is not None:
                headers['Content-Type'] = 'application/octet-stream'
            request = urllib.request.Request(BASE + path, headers=headers, data=body)
            try:
                with self.opener.open(request, timeout=5 if command == 'status' else 180) as response:
                    raw = response.read(65537)
                    if len(raw) > 65536:
                        raise ConnectorError('BRIDGE_RESPONSE_INVALID')
                    value = json.loads(raw)
            except urllib.error.HTTPError as error:
                # A 403 is checked before mutations; reread the per-process token once.
                if error.code == 403 and attempt == 0:
                    error.close()
                    continue
                code = 'NATIVE_AUTH_REJECTED' if error.code == 403 else 'BRIDGE_RESPONSE_INVALID'
                if error.code in (400, 409, 502, 503):
                    code = {400: 'INVALID_SESSION', 409: 'LOGIN_REQUIRED', 502: 'UPSTREAM_ERROR', 503: 'SYNC_FAILED'}[error.code]
                if error.code == 409 and target == 'academic':
                    # Only the exact known conflict code is accepted. Never
                    # forward upstream text or replay a connection after 409.
                    try:
                        failure = json.loads(error.read(65537))
                        if failure.get('error', {}).get('code') == 'PLAN_CONFLICT':
                            code = 'PLAN_CONFLICT'
                    except (ValueError, TypeError, AttributeError):
                        pass
                error.close()
                raise ConnectorError(code, code in ('UPSTREAM_ERROR', 'SYNC_FAILED')) from None
            except (socket.timeout, TimeoutError):
                raise ConnectorError('CONNECTION_OUTCOME_UNKNOWN' if command in ('connect', 'check') else 'BRIDGE_OFFLINE', command == 'status') from None
            except urllib.error.URLError as error:
                code = 'CONNECTION_OUTCOME_UNKNOWN' if command in ('connect', 'check') and isinstance(error.reason, (socket.timeout, TimeoutError)) else 'BRIDGE_OFFLINE'
                raise ConnectorError(code, code == 'BRIDGE_OFFLINE') from None
            except (ValueError, UnicodeError, RecursionError):
                raise ConnectorError('BRIDGE_RESPONSE_INVALID') from None
            except (ConnectionError, OSError):
                raise ConnectorError('CONNECTION_OUTCOME_UNKNOWN' if command in ('connect', 'check') else 'BRIDGE_OFFLINE', command == 'status') from None
            if not isinstance(value, dict) or value.get('apiVersion') != '1' or value.get('error') is not None:
                raise ConnectorError('BRIDGE_RESPONSE_INVALID')
            return self._safe_result(value, command)
        raise ConnectorError('NATIVE_AUTH_REJECTED', True)

    @staticmethod
    def _safe_result(value, command):
        data = value.get('data')
        if not isinstance(data, dict):
            raise ConnectorError('BRIDGE_RESPONSE_INVALID')
        result = {'clientVersion': 2, 'state': safe_meta(value.get('meta')), 'counts': {}}
        counts = data if command == 'connect' else data.get('counts', data)
        if not isinstance(counts, dict):
            raise ConnectorError('BRIDGE_RESPONSE_INVALID')
        for key in ('courses', 'categories', 'exams', 'publishedExams', 'assignments', 'files', 'pending', 'courseCount', 'examCount', 'publishedExamCount'):
            if key in counts:
                if type(counts[key]) is not int or not 0 <= counts[key] <= 1000000:
                    raise ConnectorError('BRIDGE_RESPONSE_INVALID')
                result['counts'][key] = counts[key]
        if command == 'connect':
            if (data.get('connected') is not True or not isinstance(data.get('updatedAt'), str)
                    or data.get('updatedAt') != result['state']['updatedAt']):
                raise ConnectorError('BRIDGE_RESPONSE_INVALID')
            result['connected'] = True
        if command == 'verify':
            if data.get('verified') is not True:
                raise ConnectorError('BRIDGE_RESPONSE_INVALID')
            result['verified'] = True
        if command == 'check':
            refresh = data.get('refresh')
            if (type(data.get('validated')) is not bool or data.get('checkVersion') != 2
                    or not isinstance(refresh, dict) or type(refresh.get('accepted')) is not bool
                    or refresh.get('reason') not in ('started', 'already_running', 'cooldown')
                    or type(refresh.get('retryAfterSeconds')) is not int
                    or not 0 <= refresh['retryAfterSeconds'] <= 3600):
                raise ConnectorError('BRIDGE_RESPONSE_INVALID')
            result['validated'] = data['validated']
            result['checkVersion'] = 2
            result['refresh'] = {key: refresh[key] for key in ('accepted', 'reason', 'retryAfterSeconds')}
        return result
