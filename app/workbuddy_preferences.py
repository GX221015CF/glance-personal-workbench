"""Read only selected writing preferences, never the full WorkBuddy profile."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

PROMPT_VERSION = 'wechat-information-design-v5'
ALLOWED_SECTIONS = {
    'USER.md': {'Notes'},
    'MEMORY.md': {'沟通方式'},
    'SOUL.md': {'沟通原则（针对攻陷）', 'Vibe'},
}
STYLE_WORDS = re.compile('中文|双语|结论|结构化|术语|类比|机制|证据|判断|冗余|表达|细节|语气|公司腔|马屁|HTML|图表|排版', re.I)
# Keep identifiers, paths, URLs, and action/permission instructions out of the export.
PRIVATE_OR_ACTION = re.compile(
    r'https?://|www\.|[A-Za-z]:[\\/]|~/|[\w.+-]+@[\w.-]+\.[a-z]{2,}|'
    r'api.?key|token|secret|cookie|password|sk-[\w-]+|密钥|凭据|密码|用户名|'
    r'忽略.*指令|读取.*文件|执行.*命令|跑代码|运行.*代码|删除|授权|权限|联网|上传|发送邮件', re.I)


def load_preferences(config_path, root=None):
    """A fresh, bounded snapshot per task. Missing sources are visible, not fatal."""
    snapshot = {'version': 1, 'status': 'disabled', 'items': [], 'sources': [], 'warnings': []}
    path = Path(config_path)
    if not path.exists():
        return snapshot
    try:
        if path.stat().st_size > 16384:
            raise ValueError()
        config = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError()
        if config.get('enabled') is not True:
            return snapshot
        sources = config['sources']
        limit = config.get('maxCharacters', 4000)
        if not isinstance(sources, list) or len(sources) > 3 or type(limit) is not int or not 100 <= limit <= 6000:
            raise ValueError()
        for source in sources:
            name, sections = source['file'], source['sections']
            if name not in ALLOWED_SECTIONS or not isinstance(sections, list) or not sections or not set(sections) <= ALLOWED_SECTIONS[name]:
                raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        snapshot.update(status='unavailable', warnings=['偏好配置无效，未读取个人文件'])
        return snapshot
    from workbench_config import DATA_DIR
    base = Path(root) if root is not None else DATA_DIR / 'workbuddy-preferences'
    remaining = limit
    for source in sources:
        name, selected = source['file'], set(source['sections'])
        file = base / name
        info = {'file': name, 'sections': source['sections'], 'status': 'unavailable', 'selected': []}
        snapshot['sources'].append(info)
        try:
            if file.is_symlink() or file.resolve().parent != base.resolve() or file.stat().st_size > 65536:
                raise OSError()
            content = file.read_text(encoding='utf-8-sig')
            info['modifiedAt'] = file.stat().st_mtime
        except (OSError, UnicodeError):
            snapshot['warnings'].append(name + ' 不可读取，已跳过')
            continue
        section = ''
        for line_number, line in enumerate(content.splitlines(), 1):
            heading = re.match(r'^#{1,6}\s+(.+?)\s*$', line)
            notes = re.match(r'^-\s+\*\*Notes:\*\*\s*$', line)
            if heading or notes:
                section = heading.group(1) if heading else 'Notes'
                continue
            if section not in selected or not line.strip():
                continue
            # USER Notes is a nested list; stop at the next top-level field.
            if name == 'USER.md' and line.startswith('- '):
                section = ''
                continue
            value = re.sub(r'^\s*[-*]\s+', '', line).strip().replace('**', '')
            if not STYLE_WORDS.search(value) or PRIVATE_OR_ACTION.search(value) or len(value) > 600:
                continue
            if value in snapshot['items']:
                continue
            if len(value) > remaining:
                snapshot['warnings'].append('偏好已达到长度上限，其余内容未传入')
                break
            remaining -= len(value)
            snapshot['items'].append(value)
            info['selected'].append({'line': line_number, 'text': value})
        info['status'] = 'loaded'
        exported = json.dumps(info['selected'], ensure_ascii=False, sort_keys=True)
        info['selectedSha256'] = hashlib.sha256(exported.encode('utf-8')).hexdigest()
    snapshot['status'] = ('partial' if snapshot['warnings'] else 'loaded') if snapshot['items'] else 'unavailable'
    return snapshot


def execution_summary(snapshot):
    """Model selection is unknown unless the MCP footer explicitly reports it."""
    result = snapshot.get('result') or {}
    if not isinstance(result, dict):
        result = {}
    summary = {'status': snapshot.get('status', 'unknown'), 'reportedModel': None,
               'modelEvidence': 'not_returned', 'resultIsError': bool(result.get('isError')),
               'sessionId': snapshot.get('session_id'), 'toolTracePresent': False}
    for block in result.get('content') or []:
        if not isinstance(block, dict) or block.get('type') != 'text':
            continue
        matches = re.findall(r'^— model: ([A-Za-z0-9:._/-]+)(?: → ([A-Za-z0-9:._/-]+))? · approval: workspace-write(?: ·.*)?$', block.get('text', ''), re.M)
        if matches:
            requested, resolved = matches[-1]
            summary['reportedModel'] = resolved or (requested if requested != 'auto' else None)
            summary['modelEvidence'] = 'mcp_cli_footer' if summary['reportedModel'] else 'auto_not_resolved'
    meta = result.get('_meta') or {}
    trace = meta.get('workbuddy/tool_trace') if isinstance(meta, dict) else None
    if isinstance(trace, dict):
        summary['toolTracePresent'] = True
        # Trace is emitted CLI diagnostics, never proof of complete access coverage.
        summary['traceComplete'] = False
    return summary
