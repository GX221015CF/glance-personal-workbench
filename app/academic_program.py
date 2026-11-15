"""One fixed official program definition, separate from the personal academic plan.

No web input, student identifier, arbitrary URL, or daily refresh entry point.
"""
import collections
import hashlib
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup
from gmail_bridge import protect
from school_storage import private_root, save_encrypted_json
from sufe_bridge import BASE, LoginRequired, SufeError, load_connection, make_session, now

PLAN_ID = '56316'
SOURCE = BASE + 'allMajorPlanSearch!info.action?planId=' + PLAN_ID
STORE = private_root('Academic') / ('program-' + PLAN_ID + '.dpapi')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def parse_program(markup):
    soup = BeautifulSoup(markup, 'html.parser')
    table = soup.select_one('table#planInfoTable')
    if not table or table.get('name') != 'planInfoTable' + PLAN_ID:
        raise SufeError('官方方案表结构或计划标识不符，未保存')
    rows = []
    for tr in table.find_all('tr'):
        columns = []
        for cell in tr.find_all(['td', 'th'], recursive=False):
            columns += [cell.get_text(' ', strip=True)] + [''] * (int(cell.get('colspan', 1)) - 1)
        if not columns or columns[0] in ('分类', '1', '备注'):
            continue
        if len(columns) != 16 or not re.fullmatch(r'\d+(?:\.\d+)?', columns[5]):
            raise SufeError('官方方案行缺失或变化，未生成完整定义')
        code = columns[3] if re.fullmatch(r'\d{6}', columns[3]) else None
        labels = [c for c in columns[:5] if c and c not in ('学分小计', '选修学分')]
        if code:
            labels = [c for c in columns[:3] if c]
        rows.append({'kind': 'course' if code else 'requirement', 'path': labels,
                     'code': code, 'name': columns[4] if code else ' / '.join(labels),
                     'credits': float(columns[5]), 'termAllocationRaw': columns[6:14],
                     'department': columns[14], 'note': columns[15],
                     'subtotal': any(c in ('学分小计', '选修学分') for c in columns[:5]),
                     'rawColumns': columns})
    text = soup.get_text(' ', strip=True)
    def field(pattern):
        match = re.search(pattern, text)
        if not match:
            raise SufeError('官方适用信息不完整，未保存')
        return match.group(1).strip()
    applicability = {'level': field(r'(大学本科)\s'), 'studentType': field(r'(本科生)\s'),
                     'department': field(r'本科生\s+([^\s]+学院)'),
                     'major': field(r'学院\s+([^\s]+)专业'), 'durationYears': field(r'学制：\s*(\d+)'),
                     'effectiveFrom': field(r'生效日期：\s*([\d-]+)'),
                     'effectiveUntil': field(r'失效日期：\s*([\d-]+)'),
                     'schoolModifiedAt': field(r'最后修改时间：\s*([\d-]+\s+\d+:\d+)')}
    totals = [r for r in rows if r['path'] == ['全程总计']]
    if len(totals) != 1 or not any(r['kind'] == 'course' for r in rows):
        raise SufeError('官方方案总计或课程缺失，未保存')
    choices = [r for r in rows if r['note'] == '从七个模块中选修四个模块']
    computers = [r for r in rows if r['kind'] == 'course' and r['note'] == '二选一']
    if len(choices) != 1 or len(computers) != 2:
        raise SufeError('候选规则变化，需重新核对原文')
    definition = {'planId': PLAN_ID, 'source': SOURCE, 'applicability': applicability,
                  'totalRequiredCredits': totals[0]['credits'], 'rows': rows,
                  'rules': {'generalChoice': {'candidates': 7, 'required': 4, 'credits': choices[0]['credits'],
                                              'evidence': choices[0]['note']},
                            'computerChoice': {'requiredCourses': 1, 'candidateCodes': [r['code'] for r in computers],
                                               'evidence': '二选一'}},
                  'sourceText': text, 'tableHtml': str(table)}
    return definition


def read_saved(path=STORE):
    if not path.exists():
        return None
    saved = json.loads(protect(path.read_bytes(), decrypt=True))
    if saved.get('version') != 1 or digest(saved.get('definition')) != saved.get('fingerprint'):
        raise ValueError('INVALID_FIXED_PROGRAM')
    return saved


def obtain_once(path=STORE):
    saved = read_saved(path)
    if saved:
        return saved
    bundle = load_connection().get('bundle')
    if not bundle:
        raise LoginRequired('请先通过现有学校连接恢复教务会话')
    with make_session(bundle) as session:
        response = session.get(SOURCE, timeout=25, allow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308, 401, 403):
            raise LoginRequired('官方方案需要有效教务会话，旧计划保持不变')
        if response.status_code != 200 or len(response.content) > 2_000_000:
            raise SufeError('官方方案读取失败')
        definition = parse_program(response.content)
    stamp = now()
    saved = {'version': 1, 'definition': definition, 'fingerprint': digest(definition),
             'firstFetchedAt': stamp, 'verifiedAt': stamp,
             'scope': '指定官方计划表、元数据及原文；与个人固定计划分开保存'}
    try:
        save_encrypted_json(path, saved, create_only=True)
    except FileExistsError:
        return read_saved(path)
    return read_saved(path)


def map_personal(definition, personal):
    """Candidate links only. Codes preserve multiplicity and original membership."""
    by_code = collections.defaultdict(list)
    for index, row in enumerate(definition['rows']):
        if row['code']:
            by_code[row['code']].append(index)
    links = []
    for row in personal['definition']['courses']:
        matches = by_code.get(row['code'], [])
        links.append({'code': row['code'], 'personalGroupId': row['groupId'], 'programRows': matches,
                      'relation': 'exact_code_candidate' if len(matches) == 1 else
                                  'duplicate_requires_review' if matches else 'not_in_coded_catalog',
                      'recognizedAsRequirement': False})
    return links


def map_categories(definition, personal):
    """Exact structural labels only, never join similarly numbered modules."""
    candidates = collections.defaultdict(list)
    for index, row in enumerate(definition['rows']):
        if row['kind'] != 'requirement':
            continue
        path = row['path']
        name = None
        if path[0] == '通识教育课程' and len(path) > 1:
            if path[1] == '通识限定选修课' and not row['subtotal']:
                name = row['rawColumns'][3]
            elif path[1] == '通识模块七' and not row['subtotal']:
                name = path[1] + '-' + row['rawColumns'][3]
            elif path[1] != '通识限定选修课':
                name = '-'.join(path[1:])
        elif path[0] in ('学科共同课', '专业课') and len(path) == 2:
            name = '-'.join(path)
        elif path[0] == '个性化培养课程' and len(path) == 3:
            name = '-'.join(path[1:])
        elif path[0] == '第二课堂' and len(path) == 2:
            name = path[1]
        elif path[0] in ('毕业论文', '毕业实习'):
            name = path[0]
        if name:
            candidates[name].append(index)
    result = []
    for group in personal['definition']['categories']:
        matches = candidates.get(group['name'], [])
        relation = 'unresolved'
        if len(matches) == 1:
            relation = ('exact_label_and_credits' if definition['rows'][matches[0]]['credits'] == group['requiredCredits']
                        else 'credit_conflict')
        result.append({'personalGroupId': group['id'], 'name': group['name'], 'programRows': matches,
                       'relation': relation, 'schoolClassificationPreserved': True})
    return result


if __name__ == '__main__':
    saved = obtain_once()
    definition = saved['definition']
    print(json.dumps({'planId': definition['planId'], 'rows': len(definition['rows']),
                      'codedCourses': sum(r['kind'] == 'course' for r in definition['rows']),
                      'totalRequiredCredits': definition['totalRequiredCredits'],
                      'fingerprint': saved['fingerprint'], 'firstFetchedAt': saved['firstFetchedAt']}, ensure_ascii=False))
