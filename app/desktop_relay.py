"""Short-lived, fixed-origin handoff from the desktop to the existing Edge connector.

Capabilities authorize only one scoped school-update task. No URL, cookie or
command-line payload crosses this boundary. Restart discards all capabilities.
"""
import copy
import json
import re
import secrets
import threading
import time

from school_connector.client import safe_meta

PATH = '/api/desktop-relay/v1/control'
PHASES = {'idle', 'queued', 'opening', 'checking', 'sso_pending', 'connecting', 'waiting',
          'succeeded', 'partial', 'cooldown', 'failed', 'unknown', 'cancelled'}
COMMANDS = {'continueSchool', 'retrySchool', 'cancel', 'status', 'openPortal', 'openSchool'}


def safe_state(value, targets):
    if not isinstance(value, dict) or value.get('targets') != targets:
        raise ValueError('INVALID_STATE')
    result = {key: value.get(key) is True for key in ('running', 'canCancel', 'canCheck', 'canContinue',
              'canRetry', 'canStart', 'canDismiss', 'portalWaiting', 'portalCanOpen', 'updating', 'unified', 'cancelled', 'scheduled')}
    result['taskId'] = value.get('taskId')
    result['revision'] = value.get('revision') if type(value.get('revision')) is int else 0
    result['outcome'] = value.get('outcome') if value.get('outcome') in (
        'idle', 'running', 'waiting', 'succeeded', 'partial', 'failed', 'unknown', 'cancelled') else 'unknown'
    result['sourcePage'] = value.get('sourcePage') if value.get('sourcePage') in ('/study', '/academic') else None
    result['createdAt'] = value.get('createdAt') if type(value.get('createdAt')) in (int, float) else None
    stamp = value.get('completedAt')
    result['completedAt'] = stamp if isinstance(stamp, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T[0-9:+.-]{8,30}', stamp) else None
    if not isinstance(result['taskId'], str) or not re.fullmatch(r'[a-f0-9-]{36}', result['taskId']):
        raise ValueError('INVALID_STATE')
    result.update(targets=targets, systems={}, pageTargets=targets, foreignTask=False)
    for target in targets:
        row = value.get('systems', {}).get(target, {})
        if row.get('phase') not in PHASES:
            raise ValueError('INVALID_STATE')
        clean = {key: row.get(key) for key in ('phase', 'retryAt', 'scheduled', 'connectionPath', 'canOpen')}
        code = row.get('code')
        clean['code'] = code if isinstance(code, str) and re.fullmatch(r'[A-Z_]{1,60}', code) else None
        data = row.get('result')
        if data:
            clean['result'] = {'state': safe_meta(data.get('state')), 'counts': {
                key: number for key, number in data.get('counts', {}).items()
                if key in ('courses', 'categories', 'exams', 'assignments', 'files', 'courseCount', 'examCount')
                and type(number) is int and 0 <= number <= 1000000}}
        result['systems'][target] = clean
    return result


class DesktopRelay:
    def __init__(self):
        self.records = {}
        self.lock = threading.Lock()

    def control(self, value, native, services):
        with self.lock:
            stamp = time.monotonic()
            self.records = {key: row for key, row in self.records.items() if row['expires'] > stamp}
            action = value.get('action')
            if action == 'create':
                if not native or set(value) != {'action', 'page', 'confirmed'} or value['page'] not in ('/study', '/academic'):
                    raise ValueError('INVALID_REQUEST')
                if len(self.records) >= 4:
                    raise ValueError('RELAY_BUSY')
                targets = ['sufe', 'academic'] if value['page'] == '/academic' else ['sufe', 'canvas', 'academic']
                confirmed = {}
                for target, data in value['confirmed'].items():
                    if target not in targets:
                        raise ValueError('INVALID_TARGET')
                    meta = safe_meta(data.get('state'))
                    actual = services[target].api_snapshot('status')['meta']
                    if (not meta['available'] or meta['stale'] or meta['needsLogin'] or meta['refreshing']
                            or meta['errorCode'] or not meta['updatedAt'] or meta['updatedAt'] != actual.get('updatedAt')):
                        raise ValueError('UNCONFIRMED_RESULT')
                    confirmed[target] = {'clientVersion': 2, 'state': meta, 'counts': {}}
                ticket = secrets.token_urlsafe(32)
                self.records[ticket] = {'expires': stamp + 600, 'page': value['page'], 'targets': targets,
                                        'confirmed': confirmed, 'state': None, 'reportedAt': None, 'command': None, 'sequence': 0}
                return {'ticket': ticket, 'page': value['page']}
            ticket = value.get('ticket')
            if not isinstance(ticket, str) or ticket not in self.records:
                raise ValueError('RELAY_EXPIRED')
            row = self.records[ticket]
            if action == 'view' and native:
                return {'state': copy.deepcopy(row['state']), 'reportedAt': row['reportedAt']}
            if action == 'command' and native and value.get('command') in COMMANDS:
                if value['command'] == 'openSchool' and value.get('target') not in row['targets']:
                    raise ValueError('INVALID_TARGET')
                if row['command']:
                    return {'queued': True}
                row['sequence'] += 1
                row['command'] = {'id': row['sequence'], 'command': value['command'], 'target': value.get('target')}
                return {'queued': True}
            if action == 'claim' and not native:
                return {'page': row['page'], 'confirmed': copy.deepcopy(row['confirmed'])}
            if action == 'next' and not native:
                command = row['command']
                # Delivery is at most once. A lost response is reconciled by status,
                # never by replaying a mutation blindly.
                row['command'] = None
                return {'command': command}
            if action == 'report' and not native:
                clean = safe_state(value.get('state'), row['targets'])
                if row['state'] and clean['taskId'] != row['state']['taskId']:
                    raise ValueError('TASK_CHANGED')
                row['state'] = clean
                row['reportedAt'] = time.time() * 1000
                return {'received': True}
            raise ValueError('INVALID_REQUEST')


RELAY = DesktopRelay()


def handle_request(handler, services, authorize):
    if handler.path != PATH:
        return False
    native = authorize(handler)
    if handler.command != 'POST' or not (native or handler.desktop_request()):
        handler.json_response(403, {'error': {'code': 'FORBIDDEN'}})
        return True
    length = handler.headers.get('Content-Length', '0')
    if handler.headers.get('Transfer-Encoding') or not length.isdecimal() or not 1 <= int(length) <= 32768:
        handler.json_response(400, {'error': {'code': 'INVALID_REQUEST'}})
        return True
    try:
        handler.connection.settimeout(5)
        value = json.loads(handler.rfile.read(int(length)))
        if not isinstance(value, dict):
            raise ValueError()
        result = RELAY.control(value, native, services)
        handler.json_response(200, {'data': result})
    except Exception:
        handler.json_response(400, {'error': {'code': 'RELAY_REQUEST_REJECTED'}})
    return True
