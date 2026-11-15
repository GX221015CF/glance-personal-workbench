"""Exercise the rendered pages, assets and empty authorization states."""
import json
from pathlib import Path
import re
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def check_http():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    checked = []

    def get(base, path, headers=None):
        request = urllib.request.Request(base + path, headers=headers or {})
        with opener.open(request, timeout=15) as response:
            assert response.status == 200, path
            checked.append(path)
            return response.read()

    web = 'http://127.0.0.1:8180'
    bridge = 'http://127.0.0.1:8182'
    html = ''
    for slug in ('today', 'study', 'academic', 'wechat', 'gmail', 'youtube'):
        shell = get(web, '/' + slug).decode('utf8')
        content = get(web, '/api/pages/' + slug + '/content/').decode('utf8')
        assert len(content) > 100, slug
        html += shell + content
    assets = set(re.findall(r'''(?:src|href)=["'](/assets/[^"'#]+)''', html))
    for path in sorted(assets):
        get(web, path)
    session = json.loads(get(bridge, '/api/desktop', {'Origin': web}))
    assert not any(session['actions'].values())
    headers = {'Origin': web, 'X-Desktop-Token': session['token']}
    for path in ('/api/gmail', '/api/youtube'):
        value = json.loads(get(bridge, path, headers))
        assert value['configured'] is False and value['accounts'] == [], path
    for path in ('/api/sufe/v1/status', '/api/canvas/v1/status', '/api/academic/v1/status'):
        value = json.loads(get(bridge, path, headers))
        assert value['meta']['needsLogin'] is True, path
        assert value['meta']['available'] is False, path
    assert json.loads(get(bridge, '/api/ima/v1/status', headers))['data']['configured'] is False
    assert json.loads(get(bridge, '/api/wechat', headers))['articles'] == []
    try:
        get(bridge, '/api/gmail')
        raise AssertionError('Metadata route accepted a request without its local token')
    except urllib.error.HTTPError as error:
        assert error.code == 403
    result = {'pageShells': 6, 'pageContents': 6, 'assetRequests': len(assets),
              'emptyAccountAndLoginStates': True, 'unauthorizedMetadata': 403,
              'successfulRequests': len(checked)}
    output = ROOT / '.local' / 'verification'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'http.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    print('HTTP检查通过 ' + json.dumps(result, ensure_ascii=False), flush=True)
    return result


if __name__ == '__main__':
    check_http()
