"""Fixed saved-session checks. No cookies, URLs or identities from callers."""
import time
from school_connection_audit import record


PREFIX = '/api/school-connector/v1/check/'


def handle_request(handler, services, native_authorize):
    if not handler.path.startswith(PREFIX):
        return False
    target = handler.path[len(PREFIX):]
    if handler.command != 'POST' or target not in services:
        handler.json_response(405, {'error': {'code': 'METHOD_NOT_ALLOWED'}})
        return True
    if not native_authorize(handler):
        handler.json_response(403, {'error': {'code': 'FORBIDDEN'}})
        return True
    length = handler.headers.get('Content-Length', '0')
    if handler.headers.get('Transfer-Encoding') or not length.isdecimal() or int(length):
        handler.json_response(400, {'error': {'code': 'INVALID_REQUEST'}})
        return True
    service = services[target]
    before = service.snapshot()
    result = ({'accepted': False, 'reason': 'already_running', 'retryAfterSeconds': 0}
              if before.get('refreshing') else service.request_refresh())
    deadline = time.monotonic() + 160
    while service.snapshot().get('refreshing') and time.monotonic() < deadline:
        time.sleep(.15)
    payload = service.api_snapshot('status')
    meta = payload['meta']
    # A new update must advance the actual success marker. Cooldown is not
    # another successful read, even when the saved session worked recently.
    payload['data']['checkVersion'] = 2
    payload['data']['refresh'] = {key: result.get(key) for key in ('accepted', 'reason', 'retryAfterSeconds')}
    payload['data']['validated'] = bool(meta.get('available') and not meta.get('stale')
                                        and not meta.get('needsLogin') and not meta.get('refreshing')
                                        and not meta.get('errorCode') and meta.get('checkedAt')
                                        and meta.get('updatedAt') and meta['updatedAt'] != before.get('updatedAt')
                                        and result.get('reason') in ('started', 'already_running'))
    record(target, 'saved_check', payload['data'], meta)
    handler.json_response(200, payload)
    return True
