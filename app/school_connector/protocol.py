"""Version 1 messages. Fixtures in tests are synthetic, never real sessions."""
import json
import re
import struct

MAX_MESSAGE = 64 * 1024
HEADER = struct.Struct('=I')  # 4 bytes, native byte order (Windows is little endian).
TARGETS = ('sufe', 'canvas', 'academic')
ID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')


class ConnectorError(Exception):
    def __init__(self, code, retryable=False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


def _read_exact(stream, length):
    chunks = bytearray()
    while len(chunks) < length:
        part = stream.read(length - len(chunks))
        if not part:
            raise ConnectorError('TRUNCATED_FRAME')
        chunks.extend(part)
    return bytes(chunks)


def read_frame(stream):
    first = stream.read(4)
    if not first:
        return None
    if len(first) != 4:
        first += _read_exact(stream, 4 - len(first))
    length = HEADER.unpack(first)[0]
    if not 1 <= length <= MAX_MESSAGE:
        raise ConnectorError('FRAME_SIZE')
    return _read_exact(stream, length)


def write_frame(stream, value):
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if not 1 <= len(raw) <= MAX_MESSAGE:
        raise ConnectorError('FRAME_SIZE')
    stream.write(HEADER.pack(len(raw)))
    stream.write(raw)
    stream.flush()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def parse_json(raw):
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, RecursionError):
        raise ConnectorError('INVALID_MESSAGE') from None


def validate_session(target, session):
    if not isinstance(session, dict) or set(session) != {'cookies'}:
        raise ConnectorError('INVALID_SESSION')
    cookies = session['cookies']
    if not isinstance(cookies, list) or not 1 <= len(cookies) <= (2 if target == 'canvas' else 30):
        raise ConnectorError('INVALID_SESSION')
    expected = 'canvas.shufe.edu.cn' if target == 'canvas' else 'eams.sufe.edu.cn'
    result, seen = [], set()
    for item in cookies:
        if (not isinstance(item, dict) or not {'name', 'value', 'domain'} <= set(item)
                or set(item) - {'name', 'value', 'domain', 'path', 'secure', 'httpOnly'}):
            raise ConnectorError('INVALID_SESSION')
        name, value, domain = item['name'], item['value'], item['domain']
        path, secure = item.get('path', '/'), item.get('secure', True)
        if (not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", name)
                or not isinstance(value, str) or not 1 <= len(value) <= 8192
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
                or not isinstance(domain, str) or domain not in (expected, '.' + expected)
                or not isinstance(path, str) or not path.startswith('/') or len(path) > 512
                or any(ord(char) < 32 for char in path) or type(secure) is not bool
                or ('httpOnly' in item and type(item['httpOnly']) is not bool)):
            raise ConnectorError('INVALID_SESSION')
        if target == 'canvas' and (name not in {'_normandy_session', '_legacy_normandy_session'} or path != '/'):
            raise ConnectorError('INVALID_SESSION')
        if target == 'academic' and path not in ('/', '/tch', '/tch/'):
            raise ConnectorError('INVALID_SESSION')
        key = (name, domain.lstrip('.'), path)
        if key in seen:
            raise ConnectorError('INVALID_SESSION')
        seen.add(key)
        result.append({'name': name, 'value': value, 'domain': domain, 'path': path, 'secure': secure})
    return {'cookies': result}


def validate_request(value):
    if not isinstance(value, dict):
        raise ConnectorError('INVALID_MESSAGE')
    command = value.get('command')
    required = {'version', 'id', 'command', 'target'} | ({'session'} if command in ('connect', 'verify') else set())
    if (set(value) != required or type(value.get('version')) is not int or value['version'] != 1
            or not isinstance(value.get('id'), str) or not ID.fullmatch(value['id'])
            or command not in ('status', 'connect', 'verify', 'check')
            or value.get('target') not in (*TARGETS, 'all')
            or (command in ('connect', 'check') and value['target'] == 'all')
            or (command == 'verify' and value['target'] != 'academic')):
        raise ConnectorError('INVALID_MESSAGE')
    if command in ('connect', 'verify'):
        return {**value, 'session': validate_session(value['target'], value['session'])}
    return value


MESSAGES = {
    'PLAN_CONFLICT': '学校培养要求或本人归属有变化，原计划和进度已保留，请核对后处理；无需反复登录。',
    'INVALID_MESSAGE': '消息格式或命令无效。', 'INVALID_SESSION': '目标系统会话格式无效。',
    'FRAME_SIZE': '消息大小超出本机组件限制。', 'TRUNCATED_FRAME': '消息传输未完成。',
    'HOST_NOT_CONFIGURED': '尚未配置真实扩展 ID。', 'CALLER_FORBIDDEN': '扩展来源不匹配。',
    'BRIDGE_OFFLINE': '工作台数据服务未启动或无法连接。',
    'NATIVE_AUTH_UNAVAILABLE': '尚未取得当前用户的本机连接凭证，请核对服务版本和文件视图。',
    'NATIVE_AUTH_REJECTED': '本机连接凭证已失效，请重试或核对运行用户。',
    'LOGIN_REQUIRED': '学校会话已过期，请在 Edge 完成登录后重新连接。',
    'UPSTREAM_ERROR': '学校数据暂时无法读取，已保留上次有效数据。',
    'SYNC_FAILED': '工作台未能完成连接或保存，请查看本机服务状态。',
    'CONNECTION_OUTCOME_UNKNOWN': '连接结果尚不确定，后台可能仍在处理；请查询状态，勿立即重复连接。',
    'BRIDGE_RESPONSE_INVALID': '工作台响应格式不符合协议。',
    'INTERNAL_ERROR': '本机组件未能完成请求。',
}


def reply(request=None, result=None, error=None):
    response = {'version': 1, 'id': request['id'] if request else None,
                'ok': error is None, 'result': result if error is None else None, 'error': None}
    if error:
        response['error'] = {'code': error.code, 'message': MESSAGES.get(error.code, MESSAGES['INTERNAL_ERROR']),
                             'retryable': error.retryable}
    return response
