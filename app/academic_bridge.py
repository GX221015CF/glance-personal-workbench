"""Fixed, read-only SUFE academic-planning adapter. No course adjustments."""
from __future__ import annotations

import copy
import collections
import hashlib
import json
import logging
import math
import os
import re
import secrets
import threading
import time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from bs4 import BeautifulSoup
import requests

from gmail_bridge import protect
from school_connector.protocol import validate_session, ConnectorError
from school_storage import private_root
from school_connection_audit import record as record_school_connection
from sufe_bridge import now, api_envelope as school_api_envelope, SufeError, LoginRequired, InvalidSession

ORIGIN = 'https://eams.sufe.edu.cn'
BASE = ORIGIN + '/tch/studyCheck/courseFinishInfo/'
ENTRY = BASE + 'planCompletion.jsp'
QUERIES = {'plan': ('queryPlanInfo', 'planInfos'), 'courses': ('queryCourseInfoAll', 'courseInfos'),
           'identity': ('queryStdInfo', 'stdInfo'), 'supplement': ('queryType3Complete', 'complete'),
           'personalRequired': ('queryType3Required', 'score')}
PRIVATE = private_root('Academic')
OVERVIEW_FIELDS = ('requiredCredits', 'earnedCredits', 'firstClassCredits', 'otherCredits',
                   'artCredits', 'artRequiredCredits')
# Fingerprints of the reviewed executable school snippets, not credit values.
SUMMARY_FORMULA_SHA256 = 'cdd7acd51b107c2a24c2a8d3b85bc572d4a91d53ba32f78fede569eafaa9b288'
SUMMARY_ART_SHA256 = '4f5fb487cd28bb663ab03ffbc1be19ca833693d7b9a2b7bf16c07e089c833c98'


def api_envelope(data, snapshot=None, error=None):
    value = school_api_envelope(data, snapshot, error)
    if value['meta'] is not None:
        value['meta'].update(source=ENTRY, refreshIntervalSeconds=None)
        if snapshot is not None and 'creditOverview' in snapshot:
            value['meta']['creditOverview'] = copy.deepcopy(snapshot['creditOverview'])
    return value


class PlanConflict(SufeError):
    code = 'PLAN_CONFLICT'
    status = 409


def number(value):
    if isinstance(value, bool):
        raise SufeError('学校学分字段类型异常')
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise SufeError('学校学分字段缺失或格式异常') from None
    if not math.isfinite(result) or not 0 <= result <= 100000:
        raise SufeError('学校学分字段超出有效范围')
    return result


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def refresh_teaching_and_academic(teaching, academic):
    """Independent starts, not a shared success verdict. Never imports a plan."""
    def start(action):
        try:
            return action()
        except Exception:
            return {'accepted': False, 'reason': 'request_failed',
                    'error': {'code': 'SYNC_FAILED', 'message': '同步未能启动，已保留上次数据'}}
    result = start(teaching.request_refresh)
    return {**result, 'academicRefresh': start(academic.request_refresh_if_connected)}


def load_connection():
    path = PRIVATE / 'connection.dpapi'
    if not path.exists():
        return None
    value = json.loads(protect(path.read_bytes(), decrypt=True))
    if (value.get('storageVersion') != 1 or not isinstance(value.get('fixedPlan'), dict)
            or not isinstance(value.get('progress'), dict) or not isinstance(value.get('bundle'), dict)
            or value['fixedPlan'].get('fingerprint') != fingerprint(value['fixedPlan'].get('definition'))
            or value['progress'].get('planFingerprint') != value['fixedPlan']['fingerprint']):
        raise ValueError('INVALID_ACADEMIC_STORE')
    return value


def save_connection(value):
    PRIVATE.mkdir(parents=True, exist_ok=True)
    path = PRIVATE / 'connection.dpapi'
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with temporary.open('xb') as output:
            output.write(protect(json.dumps(value, ensure_ascii=False).encode('utf8')))
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def summary_source(page):
    """Recognize reviewed source without executing upstream JavaScript."""
    soup = BeautifulSoup(page, 'html.parser')
    scripts = [tag.get_text() for tag in soup.select('script:not([src])')
               if 'var totalCredits = 0;' in tag.get_text()]
    if len(scripts) != 1:
        raise ValueError('UNRECOGNIZED_SUMMARY_SOURCE')
    formula = re.search(r'var totalCredits = 0;.*?document.getElementById\("finishedOutClassroomCredits"\)'
                        r'\.innerHTML = finishedOutClassroomCredits.toString\(\);', scripts[0], re.S)
    art = re.search(r'if\(stuInfo!=null\)\{.*?(?=var (?:type3CompleteInfo|totalCredits)\s*=)', scripts[0], re.S)
    def digest(match):
        if match is None:
            raise ValueError('UNRECOGNIZED_SUMMARY_SOURCE')
        canonical = '\n'.join(line.strip() for line in match.group().splitlines()
                              if line.strip() and not line.strip().startswith('//'))
        return hashlib.sha256(canonical.encode()).hexdigest()
    if digest(formula) != SUMMARY_FORMULA_SHA256 or digest(art) != SUMMARY_ART_SHA256:
        raise ValueError('SUMMARY_SOURCE_CHANGED')
    labels = soup.find_all(string=lambda text: text and re.fullmatch(
        r'已获艺术类课程学分[（(]要求\d+(?:\.\d+)?学分[）)]', text.strip()))
    if len(labels) != 1:
        raise ValueError('ART_REQUIREMENT_MISSING')
    required = number(re.search(r'要求(\d+(?:\.\d+)?)学分', labels[0]).group(1))
    denominators = re.findall(r'\+\s*"/(\d+(?:\.\d+)?)"', art.group())
    if not denominators or any(number(value) != required for value in denominators):
        raise ValueError('ART_REQUIREMENT_CONFLICT')
    return required


def school_credit(value):
    # The school's += requires JSON numbers. A numeric string would concatenate.
    if type(value) not in (int, float):
        raise SufeError('学校总览统计输入类型异常')
    return number(value)


def school_summary(values, page):
    """Mirror the verified page formula, never sum individual course credits."""
    student = values['identity']['stdInfo'][0]
    required_rows = values['personalRequired']['score']
    if len(required_rows) != 1 or not isinstance(required_rows[0], dict):
        raise SufeError('学校个性化要求缺失或存在多条，需核对')
    result = {key: None for key in OVERVIEW_FIELDS}
    result.update(personalRequiredCredits=number(required_rows[0]['SCORE']),
                  source={'url': ENTRY, 'method': 'school_frontend_rules', 'ruleVersion': 1,
                          'formulaSha256': SUMMARY_FORMULA_SHA256, 'artSha256': SUMMARY_ART_SHA256},
                  overview={'available': False, 'stale': False, 'updatedAt': None, 'checkedAt': now(),
                            'errorCode': 'PAGE_UNAVAILABLE' if not page else 'SOURCE_CHANGED'})
    try:
        art_required = summary_source(page)
    except (ValueError, SufeError):
        return result
    try:
        required, earned, first_class, other = 0, 0, 0, 0
        for module in values['plan']['planInfos']:
            rule = module['RULETYPE']
            if rule not in ('type1', 'type2', 'type3', 'type4'):
                raise SufeError('学校总览出现未核实的分类规则')
            target = school_credit(module['COURSE_CREDITS'])
            obtained = school_credit(module['SOURCE_GETED'])
            if rule in ('type1', 'type4'):
                required += target
            counted = min(obtained, target) if rule == 'type4' else obtained
            earned += counted
            if rule == 'type4':
                other += counted
            else:
                first_class += counted
        # getType3RequiredScore uses parseInt; the fixed plan retains its original value.
        required += school_credit(student['REQUESTCREDITS']) + int(result['personalRequiredCredits'])
        transferred = sum(school_credit(row['COMPLETE_CREDITS'])
                          for row in values['supplement']['complete'] if row['STATUS'] == '已过')
        # The school counts each returned supplement row, with no code deduplication.
        first_class += transferred
        earned += transferred
        # Match positive JavaScript toFixed(2), including half ties on binary inputs.
        first_class = float(Decimal(first_class).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        earned = float(Decimal(earned).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        result.update(requiredCredits=number(required), earnedCredits=number(earned),
                      firstClassCredits=number(first_class), otherCredits=number(other),
                      artCredits=number(student['ART_CREDIT']), artRequiredCredits=art_required)
    except (KeyError, TypeError, SufeError):
        result.update({key: None for key in OVERVIEW_FIELDS})
        result['overview']['errorCode'] = 'INVALID_FIELDS'
        return result
    result['overview'].update(available=True, updatedAt=result['overview']['checkedAt'], errorCode=None)
    return result


def normalize(values, page):
    students = values['identity']['stdInfo']
    modules, raw_courses = values['plan']['planInfos'], values['courses']['courseInfos']
    if (len(students) != 1 or not isinstance(students[0], dict) or not modules or not raw_courses
            or not all(isinstance(row, dict) for row in modules + raw_courses)):
        raise SufeError('学业计划尚未分配或响应不完整')
    student = students[0]
    owner = str(student.get('STUNO') or '').strip()
    if not owner:
        raise SufeError('学校未提供可验证的本人归属')
    if any(str(row.get('STUDENT_CODE') or '').strip() != owner for row in raw_courses):
        raise PlanConflict('学校课程归属与当前本人不一致，已有计划保留')
    applicability = {key: student.get(key) for key in ('GRADE', 'DEPT_NAME', 'MAJOR_NAME', 'MAJOR_CODE', 'LEVEL_NAME',
                                                       'TRAIN_NAME', 'PROGRAM', 'SCHOOLYEARS')}
    for key in ('GRADE', 'DEPT_NAME', 'MAJOR_NAME', 'LEVEL_NAME'):
        if not isinstance(applicability[key], str) or not applicability[key].strip():
            raise SufeError('学校计划适用信息缺失')
    groups, progress_groups = [], []
    group_ids = set()
    for row in modules:
        identifier = str(row.get('COURSE_GROUP_ID') or '')
        if not identifier or identifier in group_ids or not row.get('MODULENAME'):
            raise SufeError('学校分类标识缺失或重复')
        group_ids.add(identifier)
        target, earned = number(row['COURSE_CREDITS']), number(row['SOURCE_GETED'])
        groups.append({'id': identifier, 'name': str(row['MODULENAME']), 'ruleType': str(row['RULETYPE']),
                       'parentTypeCode': row.get('PTYPECODE'), 'parentTypeName': row.get('PTYPENAME'),
                       'typeCode': row.get('NEW_TYPE_CODE'), 'courseTypeCode': row.get('COURSE_TYPE_CODE'),
                       'order': row.get('ORDERBYINPLAN'), 'requiredCredits': target,
                       'termCreditsRaw': row.get('TERM_CREDITS'),
                       'termCredits': {f'T{i}': row.get(f'T{i}') for i in range(1, 9)}})
        in_study = sum(number(course['REQUEST_CREDITS']) for course in raw_courses
                       if str(course.get('COURSE_GROUP_ID')) == identifier and course.get('STATUS') == '在读')
        progress_groups.append({'id': identifier, 'earnedCredits': earned,
                                'displayEarnedCredits': min(earned, target) if row['RULETYPE'] == 'type4' else earned,
                                'inStudyCredits': round(in_study, 4)})
    courses, catalog = [], []
    occurrences = collections.Counter()
    for row in raw_courses:
        code, group = str(row.get('COURSE_CODE') or ''), str(row.get('COURSE_GROUP_ID') or '')
        if not code or not row.get('COURSE_NAME') or not isinstance(row.get('STATUS'), str):
            raise SufeError('学校课程缺少代码、名称或原始状态')
        definition = {'code': code, 'name': str(row['COURSE_NAME']), 'groupId': group,
                      'requiredCredits': number(row['REQUEST_CREDITS']), 'typeName': row.get('COURSE_TYPE_NAME'),
                      'typeCode': row.get('COURSE_TYPE_CODE'), 'suggestedSemester': None}
        occurrences[(code, group)] += 1
        courses.append({**definition, 'rowKey': fingerprint([code, group, occurrences[(code, group)]])[:24],
                        'rawStatus': row['STATUS'], 'completedCredits': number(row['COMPLETE_CREDITS']) if row.get('COMPLETE_CREDITS') is not None else None,
                        'changeModuleRaw': row.get('CHANGEMODULE'), 'unmatchedGroup': group not in group_ids})
        catalog.append(definition)
    code_counts = collections.Counter(row['code'] for row in courses)
    supplement = []
    main_codes = set(code_counts)
    for index, row in enumerate(values['supplement']['complete']):
        if not row.get('COURSE_CODE'):
            raise SufeError('补充课程缺少代码，未合并或更新')
        supplement.append({'rowKey': str(index), 'code': str(row['COURSE_CODE']), 'name': str(row.get('COURSE_NAME') or ''),
                           'rawStatus': str(row.get('STATUS') or ''), 'completedCredits': number(row['COMPLETE_CREDITS']) if row.get('COMPLETE_CREDITS') is not None else None,
                           'inMainPlan': str(row['COURSE_CODE']) in main_codes, 'typeCode': row.get('COURSE_TYPE_CODE')})
    summary = school_summary(values, page)
    required_modules = number(student['REQUIREMODULES'])
    module_choices = [row for row in modules if row['RULETYPE'] == 'type2']
    summary['moduleChoice'] = {'candidates': len(module_choices), 'required': required_modules,
                               'completed': sum(number(row['SOURCE_GETED']) >= number(row['COURSE_CREDITS']) for row in module_choices)}
    summary['inStudyCourses'] = sum(row['rawStatus'] == '在读' for row in courses)
    definition = {'applicability': applicability, 'categories': sorted(groups, key=lambda row: row['id']),
                  'courses': sorted(catalog, key=lambda row: (row['code'], row['groupId'], row['name'])),
                  'choiceRequiredModules': required_modules, 'personalRequiredCredits': summary['personalRequiredCredits']}
    assessment = values['plan'].get('indResult')
    if not isinstance(assessment, str):
        raise SufeError('学校学业评语结构缺失')
    return {'ownerHash': fingerprint(owner), 'definition': definition,
            'progress': {'summary': summary, 'categories': progress_groups, 'courses': courses,
                         'supplement': supplement, 'assessmentRaw': assessment,
                         'counts': {'categories': len(groups), 'courses': len(courses)},
                         'statusCounts': dict(collections.Counter(row['rawStatus'] for row in courses)),
                         'diagnostics': {'duplicateCodes': sum(count > 1 for count in code_counts.values()),
                                         'unmatchedGroups': sum(row['unmatchedGroup'] for row in courses)}}}


def read(session, name):
    if name == 'page':
        response = session.get(ENTRY, timeout=20, allow_redirects=False)
    elif name in QUERIES:
        response = session.post(BASE + 'com.lg.studyCheck.planFinishInfo.' + QUERIES[name][0] + '.biz.ext',
                                headers={'X-Requested-With': 'XMLHttpRequest'}, timeout=20, allow_redirects=False)
    else:
        raise InvalidSession('不支持的学业查询')
    if response.is_redirect or response.status_code in (401, 403):
        raise LoginRequired('请在学校学业规划页面完成登录，再连接工作台')
    if response.status_code != 200 or len(response.content) > 5_000_000:
        raise SufeError('学校学业查询未返回有效结果')
    response.encoding = 'utf-8'
    if name == 'page':
        soup = BeautifulSoup(response.text, 'html.parser')
        if soup.select_one('input[type=password]'):
            raise LoginRequired('请先完成学校登录或验证')
        return response.text
    try:
        value = response.json()
    except ValueError:
        raise SufeError('学校学业查询返回了非数据页面') from None
    if not isinstance(value, dict) or not isinstance(value.get(QUERIES[name][1]), list):
        raise SufeError('学校学业数据结构不完整，保留上次结果')
    rows = value[QUERIES[name][1]]
    if (any(value.get(key) is True for key in ('hasMore', 'hasNext'))
            or any(isinstance(value.get(key), (int, float)) and value[key] > len(rows)
                   for key in ('total', 'totalCount', 'totalRecords'))):
        raise SufeError('学校查询出现分页或部分结果，未导入或更新')
    return value


def validate_bundle(bundle):
    try:
        return validate_session('academic', bundle)
    except ConnectorError:
        raise InvalidSession('仅接受学业规划固定域和路径的必要会话') from None


def fetch_raw(bundle):
    bundle = validate_bundle(bundle)
    with requests.Session() as session:
        session.trust_env = False
        session.headers.update({'User-Agent': 'PersonalWorkbench-Academic/1.0', 'Referer': ENTRY})
        for cookie in bundle['cookies']:
            session.cookies.set(cookie['name'], cookie['value'], domain=cookie['domain'],
                                path=cookie.get('path', '/'), secure=cookie.get('secure', True))
        try:
            page = read(session, 'page')
        except (requests.RequestException, SufeError):
            # The five data queries remain authoritative for authentication.
            # A failed overview source page must not discard successful course queries.
            page = ''
        values = {name: read(session, name) for name in QUERIES}
        # These are credential-free provenance diagnostics, never raw HTML/data.
        text = BeautifulSoup(page, 'html.parser').get_text(' ', strip=True)
        labels = ('要求总学分', '已获总学分', '已获第一课堂学分', '已获非第一课堂学分', '已获艺术类课程学分')
        direct = {label: bool(re.search(re.escape(label) + r'\s*[:：]?\s*\d', text)) for label in labels}
        logging.info('ACADEMIC_VERIFY categories=%d courses=%d identity=%d direct_summary=%s',
                     len(values['plan']['planInfos']), len(values['courses']['courseInfos']),
                     len(values['identity']['stdInfo']), json.dumps(direct, ensure_ascii=True))
        # Source lines are not logged: HTML may contain unrelated personal data.
        cookies = [{'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path, 'secure': c.secure}
                   for c in session.cookies]
        return values, page, validate_bundle({'cookies': cookies})


class AcademicService:
    """One authority, immutable first plan and atomically committed progress."""
    def __init__(self):
        self.lock = threading.RLock()
        self.sync_lock = threading.Lock()
        self.connection = None
        self.storage_failed = False
        self.refreshing = False
        self.last_attempt = 0
        self.state = {'available': False, 'stale': False, 'needsLogin': True, 'updatedAt': None,
                      'checkedAt': None, 'errorCode': 'LOGIN_REQUIRED', 'message': '请连接学校学业规划'}
        try:
            self.connection = load_connection()
            if self.connection:
                self.state.update(available=True, stale=True, needsLogin=False, errorCode=None,
                                  updatedAt=self.connection['progress']['updatedAt'],
                                  message='正在核验学业连接，当前为上次数据')
        except Exception:
            self.storage_failed = True
            self.state.update(errorCode='STORAGE_ERROR', message='已有学业文件无法读取，保留文件等待核对')

    def snapshot(self):
        with self.lock:
            state = copy.deepcopy(self.state)
            state['refreshing'] = self.refreshing or self.sync_lock.locked()
            state['counts'] = copy.deepcopy(self.connection['progress']['counts']) if self.connection else {}
            overview = self.connection['progress']['summary'].get('overview') if self.connection else None
            state['creditOverview'] = copy.deepcopy(overview) if overview else {
                'available': False, 'stale': False, 'updatedAt': None, 'checkedAt': None,
                'errorCode': 'NOT_ACQUIRED'}
            if state['stale'] and state['creditOverview']['available']:
                state['creditOverview']['stale'] = True
            return state

    def api_snapshot(self, resource='snapshot'):
        with self.lock:
            data = {'counts': self.snapshot()['counts']}
            if resource in ('snapshot', 'plan'):
                data['fixedPlan'] = copy.deepcopy(self.connection['fixedPlan']) if self.connection else None
            if resource in ('snapshot', 'progress'):
                data['progress'] = copy.deepcopy(self.connection['progress']) if self.connection else None
            return api_envelope(data, self.snapshot())

    def _sync(self, bundle):
        if self.storage_failed:
            raise PlanConflict('已有学业文件无法读取，需核对后才能导入')
        values, page, bundle = fetch_raw(bundle)
        current = normalize(values, page)
        old = self.connection
        definition = current['definition']
        if old:
            previous_summary = old['progress']['summary']
            current_summary = current['progress']['summary']
            if (previous_summary.get('overview', {}).get('available') and current_summary['overview']['available']
                    and any(current_summary[key] != previous_summary.get(key)
                            for key in ('requiredCredits', 'artRequiredCredits'))):
                raise PlanConflict('学校总览固定学分要求变化，已保留原计划与进度，请核对')
            saved = old['fixedPlan']['definition']
            if (current['ownerHash'] != old['ownerHash'] or definition['applicability'] != saved['applicability']
                    or definition['categories'] != saved['categories']
                    or definition['choiceRequiredModules'] != saved['choiceRequiredModules']
                    or definition['personalRequiredCredits'] != saved['personalRequiredCredits']):
                raise PlanConflict('本人归属或固定培养要求变化，已保留原计划，请核对')
            known = collections.defaultdict(list)
            for row in saved['courses']:
                known[row['code']].append(row)
            counts = collections.Counter(row['code'] for row in definition['courses'])
            for row in definition['courses']:
                if counts[row['code']] == 1 and len(known[row['code']]) == 1:
                    before = known[row['code']][0]
                    if any(row[key] != before[key] for key in ('name', 'requiredCredits', 'typeCode')):
                        raise PlanConflict('已有课程固定要求变化，已保留原计划，请核对')
            fixed = copy.deepcopy(old['fixedPlan'])
        else:
            if current['progress']['diagnostics']['unmatchedGroups']:
                raise SufeError('首次课程分类关联不完整，未生成完整计划')
            imported = now()
            fixed = {'schemaVersion': 1, 'definition': definition, 'fingerprint': fingerprint(definition),
                     'versionKind': 'local_content_fingerprint', 'schoolPlanId': None, 'schoolVersion': None,
                     'fetchedAt': imported, 'verifiedAt': imported, 'source': ENTRY,
                     'completeness': {'completeForScope': True, 'scope': 'current_school_academic_application',
                                      'officialProgramDocument': 'not_acquired', 'courseSemester': 'not_provided',
                                      'history': 'not_provided'},
                     'limitations': ['学校未提供独立计划编号和版本', '方向为空时保持未提供',
                                     '课程级建议学期、重修免修和替代历史未提供']}
        known_codes = {row['code'] for row in fixed['definition']['courses']}
        for row in current['progress']['courses']:
            row['outsideFixedCatalog'] = row['code'] not in known_codes
        progress = {**current['progress'], 'updatedAt': now(), 'planFingerprint': fixed['fingerprint']}
        summary = progress['summary']
        if summary['overview']['errorCode'] and old:
            previous = old['progress']['summary']
            if previous.get('overview', {}).get('available'):
                for key in OVERVIEW_FIELDS:
                    summary[key] = previous.get(key)
                summary['source'] = copy.deepcopy(previous['source'])
                summary['overview'].update(available=True, stale=True,
                                           updatedAt=previous['overview']['updatedAt'])
        value = {'storageVersion': 1, 'ownerHash': current['ownerHash'], 'bundle': bundle,
                 'fixedPlan': fixed, 'progress': progress}
        save_connection(value)
        with self.lock:
            self.connection = value
            self.state.update(available=True, stale=False, needsLogin=False, errorCode=None,
                              updatedAt=progress['updatedAt'], checkedAt=now(), message='学业进度已同步')
            if summary['overview']['errorCode']:
                self.state['message'] = '课程与分类已同步；学分总览未完成，' + (
                    '保留上次总览' if summary['overview']['available'] else '总览暂未取得')
        return {'connected': True, **progress['counts'], 'updatedAt': progress['updatedAt']}

    def connect_encrypted(self, encrypted):
        try:
            bundle = json.loads(protect(encrypted, decrypt=True))
        except Exception:
            raise InvalidSession('无法读取当前用户的学业会话') from None
        with self.sync_lock:
            try:
                return self._sync(validate_bundle(bundle))
            except Exception as error:
                self._record_failure(error)
                raise
            finally:
                self.last_attempt = time.monotonic()

    def _record_failure(self, error):
        with self.lock:
            self.state.update(stale=bool(self.connection), needsLogin=isinstance(error, LoginRequired),
                              checkedAt=now(), errorCode=error.code if isinstance(error, SufeError) else 'SYNC_FAILED',
                              message=str(error) if isinstance(error, SufeError) else '学业同步或保存失败，保留上次结果')

    def request_refresh_if_connected(self):
        if self.connection and not self.snapshot()['needsLogin']:
            return self.request_refresh()
        return {'accepted': False, 'reason': 'not_connected', 'retryAfterSeconds': 0}

    def request_refresh(self):
        with self.lock:
            if self.refreshing:
                return {'accepted': False, 'reason': 'already_running', 'retryAfterSeconds': 0}
            remaining = 60 - (time.monotonic() - self.last_attempt)
            if self.last_attempt and remaining > 0:
                return {'accepted': False, 'reason': 'cooldown', 'retryAfterSeconds': math.ceil(remaining)}
            self.refreshing = True
            self.last_attempt = time.monotonic()
        threading.Thread(target=self._refresh, daemon=True).start()
        return {'accepted': True, 'reason': 'started', 'retryAfterSeconds': 0}

    def _refresh(self):
        try:
            with self.sync_lock:
                if not self.connection:
                    raise LoginRequired('请先连接学业规划')
                self._sync(copy.deepcopy(self.connection['bundle']))
        except Exception as error:
            self._record_failure(error)
        finally:
            with self.lock:
                self.refreshing = False

    def verify_encrypted(self, encrypted):
        try:
            bundle = json.loads(protect(encrypted, decrypt=True))
        except Exception:
            raise InvalidSession('无法读取当前用户的学业会话') from None
        values, page, _ = fetch_raw(bundle)
        summary = school_summary(values, page)
        report = {'verified': True, 'categories': len(values['plan']['planInfos']),
                  'courses': len(values['courses']['courseInfos']), 'summary': summary,
                  'summarySource': summary['source']['method'],
                  'checkedAt': now()}
        from workbench_config import DATA_DIR
        directory = DATA_DIR / 'academic-a2'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'real-query-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        return report


def handle_request(handler, service, desktop_token, native_authorize):
    prefix = '/api/academic/v1'
    if handler.path != prefix and not handler.path.startswith(prefix + '/'):
        return False
    def fail(status, code, message):
        handler.json_response(status, api_envelope(None, service.snapshot(), {'code': code, 'message': message}))
    if not ((handler.desktop_request() and secrets.compare_digest(handler.headers.get('X-Desktop-Token', ''), desktop_token))
            or native_authorize(handler)):
        fail(403, 'FORBIDDEN', '需要有效的本机来源与凭证')
        return True
    resource = handler.path[len(prefix):].lstrip('/') or 'snapshot'
    if resource in ('status', 'snapshot') and handler.command == 'GET':
        handler.json_response(200, service.api_snapshot(resource))
        return True
    if resource == 'refresh' and handler.command == 'POST':
        length = handler.headers.get('Content-Length', '0')
        if handler.headers.get('Transfer-Encoding') or not length.isdecimal() or int(length) != 0:
            fail(400, 'INVALID_REQUEST', '同步请求不接受请求体')
            return True
        result = service.request_refresh_if_connected()
        status = 429 if result['reason'] == 'cooldown' else 202
        handler.json_response(status, api_envelope(result, service.snapshot()))
        return True
    if resource in ('verify', 'connect') and handler.command == 'POST':
        length = handler.headers.get('Content-Length', '')
        if (handler.headers.get('Transfer-Encoding') or not length.isdecimal() or not 1 <= int(length) <= 131072
                or handler.headers.get('Content-Type', '').split(';')[0] != 'application/octet-stream'):
            fail(400, 'INVALID_SESSION', '需要规定格式的本机加密会话')
            return True
        handler.connection.settimeout(10)
        try:
            body = handler.rfile.read(int(length))
            if len(body) != int(length):
                raise InvalidSession('学业会话接收不完整')
            result = (service.connect_encrypted(body) if resource == 'connect' else service.verify_encrypted(body))
            if resource == 'connect':
                record_school_connection('academic', 'session_connect', result, service.snapshot())
            handler.json_response(200, api_envelope(result, service.snapshot()))
        except SufeError as error:
            fail(error.status, error.code, str(error))
        except Exception:
            fail(503, 'SYNC_FAILED', '学业查询或保存失败，保留已有计划和进度')
        return True
    fail(405, 'METHOD_NOT_ALLOWED', '当前学业接口或方法不可用')
    return True
