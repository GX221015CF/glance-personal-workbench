"""Native Messaging entry. Browser origin is an argument, never a message field."""
import json
import os
from pathlib import Path
import re
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from school_connector.client import BridgeClient
from school_connector.protocol import ConnectorError, TARGETS, read_frame, write_frame, parse_json, validate_request, reply


def verify_caller(arguments, config_path=None):
    config_path = config_path or Path(__file__).with_name('host-config.json')
    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
        extension_id = config['extensionId']
        if config.get('version') != 1 or not isinstance(extension_id, str) or not re.fullmatch('[a-p]{32}', extension_id):
            raise ValueError()
    except Exception:
        raise ConnectorError('HOST_NOT_CONFIGURED') from None
    if (not 1 <= len(arguments) <= 2
            or arguments[0] not in ('chrome-extension://' + extension_id, 'chrome-extension://' + extension_id + '/')
            or (len(arguments) == 2 and not re.fullmatch(r'--parent-window=\d{1,20}', arguments[1]))):
        raise ConnectorError('CALLER_FORBIDDEN')


def serve(incoming, outgoing, client, max_requests=256):
    seen = set()
    for _ in range(max_requests):
        try:
            raw = read_frame(incoming)
        except ConnectorError as error:
            write_frame(outgoing, reply(error=error))
            return
        if raw is None:
            return
        request = None
        try:
            request = validate_request(parse_json(raw))
            if request['id'] in seen:
                raise ConnectorError('INVALID_MESSAGE')
            seen.add(request['id'])
            targets = TARGETS if request['target'] == 'all' else (request['target'],)
            result = {target: client.request(target, request['command'], request.get('session')) for target in targets}
            response = reply(request, result=result)
        except ConnectorError as error:
            response = reply(request, error=error)
        except Exception:
            response = reply(request, error=ConnectorError('INTERNAL_ERROR'))
        write_frame(outgoing, response)
        # Retain only request IDs between calls, not the last session or raw frame.
        raw, request = None, None


def main(arguments=None):
    if os.name == 'nt':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    try:
        verify_caller(sys.argv[1:] if arguments is None else arguments)
        serve(sys.stdin.buffer, sys.stdout.buffer, BridgeClient())
    except ConnectorError as error:
        write_frame(sys.stdout.buffer, reply(error=error))
        return 1
    except (BrokenPipeError, OSError):
        return 1
    except Exception:
        # Never print exception text, argv, payloads, tokens or traceback to stderr.
        try:
            write_frame(sys.stdout.buffer, reply(error=ConnectorError('INTERNAL_ERROR')))
        except Exception:
            pass
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
