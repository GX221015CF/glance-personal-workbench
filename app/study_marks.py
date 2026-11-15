"""Share existing, append-only local homework confirmations across web/desktop.

Canvas school submissions are untouched. Import never overwrites an already
confirmed date. Conflicting browser values are retained for explicit review.
"""
import datetime
import json
import re
import secrets
import threading

from gmail_bridge import protect
from school_storage import private_root, save_encrypted_json

PATH = '/api/desktop/study-marks'
STORE = private_root('Canvas') / 'local-study-marks.dpapi'
LOCK = threading.Lock()


def validate(records):
    if not isinstance(records, dict) or len(records) > 5000:
        raise ValueError('INVALID_MARKS')
    clean = {}
    for key, value in records.items():
        match = re.fullmatch(r'workbench-study-offline-(date|submission)-v1:\d{1,15}:\d{1,15}', key)
        if not match or not isinstance(value, dict):
            raise ValueError('INVALID_MARK')
        if match[1] == 'date':
            if set(value) != {'dueDate'}:
                raise ValueError('INVALID_DATE')
            date = value['dueDate']
            if date is not None:
                if not isinstance(date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
                    raise ValueError('INVALID_DATE')
                datetime.date.fromisoformat(date)
            clean[key] = {'dueDate': date}
        else:
            if set(value) != {'submitted', 'submittedAt'} or value['submitted'] is not True:
                raise ValueError('INVALID_SUBMISSION')
            if not isinstance(value['submittedAt'], str) or len(value['submittedAt']) > 35:
                raise ValueError('INVALID_SUBMISSION')
            datetime.datetime.fromisoformat(value['submittedAt'].replace('Z', '+00:00'))
            clean[key] = {'submitted': True, 'submittedAt': value['submittedAt']}
    return clean


def merge(records, path=STORE):
    records = validate(records)
    with LOCK:
        saved = json.loads(protect(path.read_bytes(), decrypt=True)) if path.exists() else {'version': 1, 'records': {}, 'conflicts': []}
        if saved.get('version') != 1 or not isinstance(saved.get('conflicts'), list):
            raise ValueError('INVALID_STORE')
        validate(saved['records'])
        before = json.dumps(saved, sort_keys=True)
        for key, value in records.items():
            current = saved['records'].get(key)
            if current is None or 'dueDate' in value and current.get('dueDate') is None:
                saved['records'][key] = value
            elif current != value and 'dueDate' in value and value['dueDate'] is not None:
                conflict = {'key': key, 'retained': current, 'incoming': value}
                if conflict not in saved['conflicts']:
                    if len(saved['conflicts']) >= 5000:
                        raise ValueError('TOO_MANY_CONFLICTS')
                    saved['conflicts'].append(conflict)
        validate(saved['records'])
        if json.dumps(saved, sort_keys=True) != before:
            save_encrypted_json(path, saved)
        return {'records': saved['records'], 'conflictCount': len(saved['conflicts'])}


def handle_request(handler, token):
    if handler.path != PATH:
        return False
    if not handler.desktop_request() or not secrets.compare_digest(handler.headers.get('X-Desktop-Token', ''), token):
        handler.json_response(403, {'error': {'code': 'FORBIDDEN'}})
        return True
    try:
        if handler.command == 'GET':
            records = {}
        elif handler.command == 'POST':
            length = handler.headers.get('Content-Length', '0')
            if handler.headers.get('Transfer-Encoding') or not length.isdecimal() or not 1 <= int(length) <= 1048576:
                raise ValueError()
            handler.connection.settimeout(5)
            value = json.loads(handler.rfile.read(int(length)))
            if not isinstance(value, dict) or set(value) != {'records'}:
                raise ValueError()
            records = value['records']
        else:
            raise ValueError()
        handler.json_response(200, {'data': merge(records)})
    except Exception:
        handler.json_response(503, {'error': {'code': 'MARKS_NOT_SAVED'}})
    return True
