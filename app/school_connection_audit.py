"""Best-effort, credential-free evidence for the fixed connection boundary."""
import json
from pathlib import Path
import threading

_lock = threading.Lock()
_enabled = False


def enable():
    global _enabled
    _enabled = True


def record(target, action, data, meta):
    if not _enabled:
        return
    if not isinstance(data, dict) or not isinstance(meta, dict):
        return
    if target not in ('sufe', 'canvas', 'academic') or action not in ('saved_check', 'session_connect'):
        return
    row = {'target': target, 'action': action,
           'updatedAt': meta.get('updatedAt'), 'checkedAt': meta.get('checkedAt'),
           'available': meta.get('available') is True, 'stale': meta.get('stale') is True,
           'needsLogin': meta.get('needsLogin') is True,
           'validated': data.get('validated') if type(data.get('validated')) is bool else None,
           'counts': {k: v for k, v in data.get('counts', data).items()
                      if k in ('courses', 'categories', 'courseCount', 'examCount', 'exams', 'assignments', 'files') and type(v) is int}}
    if data.get('checkVersion') == 2:
        row['checkVersion'] = 2
        reason = data.get('refresh', {}).get('reason')
        row['refreshReason'] = reason if reason in ('started', 'already_running', 'cooldown') else None
    try:
        with _lock:
            from workbench_config import DATA_DIR
            path = DATA_DIR / 'academic-a2/unified-boundary-events.jsonl'
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf8') as output:
                output.write(json.dumps(row, ensure_ascii=False) + '\n')
    except (OSError, ValueError, TypeError):
        pass  # An already-committed data save must not become a failed connect.
