"""基线适用性证书：只覆盖证据闭合的可用区间，绝不跨受阻区间补齐。

证书绑定一份“证据指纹”（规则版本 + 已采纳/隔离的遥测、事件 + 设备映射 + 规则参数）。
上游任何一项变化都会让旧版本失效，必须重新判定。
"""
import hashlib, json
from app.config import RULE_VERSION
from app.coerce import number

BLOCK_REASONS = {
    'NO_REFERENCE': '同区、同时刻、同辐照档的健康对照不足',
    'TARGET_GAP': '目标设备该区间缺测，基线不可应用',
    'TARGET_FAULT': '目标设备该区间故障，基线不可应用',
}


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def site_of(state):
    return state['context'].get('site') or '北岭光伏一场'


def scope_of(site):
    return 'CERT-' + _digest({'site': site})[:10].upper()


def _evidence_parts(state):
    records = sorted(
        ({'id': r['id'], 'device': r['device'], 'at': r.get('at', ''), 'power_kw': r.get('power_kw'),
          'irradiance': r.get('irradiance'), 'status': r.get('status')}
         for r in state['records'] if r.get('kind') == 'TELEMETRY'),
        key=lambda x: (x['device'], x['at'], x['id']))
    events = sorted(
        ({'id': r['id'], 'device': r['device'], 'start': r['start'], 'end': r['end']}
         for r in state['records'] if r.get('kind') == 'EVENT'),
        key=lambda x: x['id'])
    quarantine = sorted(
        ({'id': q.get('id', ''), 'code': q['code'], 'line': q.get('line', 0)}
         for q in state.get('quarantine', [])),
        key=lambda x: (x['code'], x['id'], x['line']))
    mapping = {d: {'zone': v.get('zone'), 'capacity_kw': str(v.get('capacity_kw')),
                   'reference': bool(v.get('reference'))}
               for d, v in state['context']['devices'].items()}
    params = {k: state['context'].get(k)
              for k in ('cadence_seconds', 'irradiance_bin', 'min_references')}
    return records, events, quarantine, mapping, params


def build_basis(state):
    """当前批次的证据指纹与作用域；证书只认这个指纹。"""
    records, events, quarantine, mapping, params = _evidence_parts(state)
    site = site_of(state)
    admission_hash = _digest({'records': records, 'events': events, 'quarantine': quarantine})
    mapping_hash = _digest(mapping)
    params_hash = _digest(params)
    # 指纹只看证据内容：同一证据换编码重导不会误伤证书，内容变化必然失效。
    fingerprint = _digest({'rule_version': RULE_VERSION, 'admission_hash': admission_hash,
                           'mapping_hash': mapping_hash, 'params_hash': params_hash})
    return {
        'scope_id': scope_of(site),
        'site': site,
        'source': state.get('source', 'dirty'),
        'rule_version': RULE_VERSION,
        'source_sha256': state.get('manifest', {}).get('sha256', ''),
        'admission_hash': admission_hash,
        'mapping_hash': mapping_hash,
        'params_hash': params_hash,
        'fingerprint': fingerprint,
        'device_mapping': mapping,
        'counts': {'records': len(records), 'events': len(events), 'quarantine': len(quarantine)},
    }


def invalidation_reason(previous, current):
    """说明旧证书为何不再适用，便于审计追证。"""
    if not previous:
        return '上游采信结果变化'
    parts = []
    if previous.get('rule_version') != current['rule_version']:
        parts.append('规则版本变化')
    if previous.get('admission_hash') != current['admission_hash']:
        parts.append('遥测采信变化')
    if previous.get('mapping_hash') != current['mapping_hash']:
        parts.append('设备映射变化')
    if previous.get('params_hash') != current['params_hash']:
        parts.append('规则参数变化')
    if not parts:
        parts.append('证据指纹变化')
    return '；'.join(parts)


def _bin_label(irradiance, width):
    if irradiance in (None, ''):
        return '辐照未定'
    low = int(number(irradiance) // width) * int(width)
    return f'{low}–{low + int(width)} W/m²'


def _entry(segment, width):
    return {
        'start': segment['start'],
        'end': segment['end'],
        'seconds': segment['seconds'],
        'irradiance': segment.get('irradiance'),
        'irradiance_bin': _bin_label(segment.get('irradiance'), width),
        'sample_id': segment.get('sample_id') or '',
        'sample_at': segment.get('sample_at') or '',
        'actual_kw': segment.get('actual_kw'),
        'baseline_kw': segment.get('baseline_kw'),
        'loss_kwh': segment.get('loss_kwh'),
        'reference_ids': list(segment.get('reference_ids') or []),
        'event_ids': list(segment.get('event_ids') or []),
        'zone': segment.get('zone'),
        'status': segment.get('status'),
    }


def build_certificate(state, basis=None):
    """按目标设备列出可用/受阻区间；可用区间逐个列出，不合并、不补齐。"""
    basis = basis or build_basis(state)
    context = state['context']
    width = number(context['irradiance_bin'])
    min_refs = context['min_references']
    rows = state.get('energy_rows') or state.get('segments', [])
    by_device = {}
    for row in rows:
        by_device.setdefault(row['device'], []).append(row)
    union_seconds = {}
    for union in state.get('event_unions', []):
        union_seconds[union['device']] = union_seconds.get(union['device'], 0) + union['seconds']

    devices = []
    total_union = total_usable = total_blocked = total_uncovered = 0
    blocked_total = 0
    for device in sorted(union_seconds):
        usable, blocked = [], []
        for segment in sorted(by_device.get(device, []), key=lambda s: s['start']):
            entry = _entry(segment, width)
            # 有基线即可用；loss 模块会把 code 改写为 LOSS/NO_LOSS，不能只看 ESTIMATED。
            if segment.get('baseline_kw') is not None and segment.get('code') in {'ESTIMATED', 'LOSS', 'NO_LOSS'}:
                usable.append({**entry, 'code': 'USABLE'})
            else:
                code = segment.get('code') or 'NO_REFERENCE'
                blocked.append({
                    **entry,
                    'code': code,
                    'reason': BLOCK_REASONS.get(code, '基线不可用'),
                    'required_refs': min_refs,
                    'available_refs': list(segment.get('reference_ids') or []),
                    'expected': {'zone': segment.get('zone'),
                                 'sample_at': segment.get('sample_at') or '',
                                 'irradiance_bin': entry['irradiance_bin']},
                })
        usable_seconds = sum(e['seconds'] for e in usable)
        blocked_seconds = sum(e['seconds'] for e in blocked)
        device_union = union_seconds[device]
        uncovered = max(0, device_union - usable_seconds - blocked_seconds)
        bins = {}
        for group, entries in (('usable', usable), ('blocked', blocked)):
            for e in entries:
                bucket = bins.setdefault(e['irradiance_bin'], {'irradiance_bin': e['irradiance_bin'],
                                                               'usable_seconds': 0, 'blocked_seconds': 0})
                bucket[f'{group}_seconds'] += e['seconds']
        devices.append({
            'device': device,
            'zone': context['devices'][device]['zone'],
            'capacity_kw': str(context['devices'][device]['capacity_kw']),
            'union_seconds': device_union,
            'usable_seconds': usable_seconds,
            'blocked_seconds': blocked_seconds,
            'uncovered_seconds': uncovered,
            'coverage': round(usable_seconds / device_union, 6) if device_union else 0,
            'certified': blocked_seconds == 0 and uncovered == 0,
            'usable': usable,
            'blocked': blocked,
            'bins': sorted(bins.values(), key=lambda b: b['irradiance_bin']),
        })
        total_union += device_union
        total_usable += usable_seconds
        total_blocked += blocked_seconds
        total_uncovered += uncovered
        blocked_total += len(blocked)

    balance = state.get('balance') or {}
    evidence_open = []
    if not balance:
        evidence_open.append('运行尚未完成，守恒账不可核验')
    if blocked_total:
        evidence_open.append(f'{blocked_total} 个受阻区间缺少可用对照')
    if total_uncovered:
        evidence_open.append(f'{total_uncovered} 秒限电区间未被切片覆盖')
    if state.get('quarantine'):
        evidence_open.append(f'{len(state["quarantine"])} 条证据处于隔离')
    if balance.get('unknown_segments'):
        evidence_open.append(f'{balance["unknown_segments"]} 个损失片段未定')
    for key, label in (('row_balanced', '行守恒'), ('time_balanced', '时间守恒'),
                       ('energy_balanced', '能量守恒')):
        if balance and not balance.get(key):
            evidence_open.append(f'{label}未通过')

    return {
        'title': '基线适用性',
        'scope_id': basis['scope_id'],
        'site': basis['site'],
        'source': basis['source'],
        'rule_version': basis['rule_version'],
        'fingerprint': basis['fingerprint'],
        'devices': devices,
        'totals': {
            'targets': len(devices),
            'union_seconds': total_union,
            'usable_seconds': total_usable,
            'blocked_seconds': total_blocked,
            'uncovered_seconds': total_uncovered,
            'coverage': round(total_usable / total_union, 6) if total_union else 0,
        },
        # 只签可用区间；受阻区间逐条列出并从覆盖中排除，不做相邻补齐。
        'policy': {'coverage': 'explicit_usable_segments_only', 'interpolate': False,
                   'blocked_excluded': True},
        'closed': not evidence_open,
        'blockers': evidence_open,
        'quarantine_ids': sorted(q.get('id', '') for q in state.get('quarantine', [])),
        'evidence': {'admission_hash': basis['admission_hash'],
                     'mapping_hash': basis['mapping_hash'],
                     'params_hash': basis['params_hash'],
                     'source_sha256': basis['source_sha256']},
    }


def build_issued(draft, basis, run_id, version, mode, note, issued_at):
    return {
        'certificate_id': f'{draft["scope_id"]}-v{version}',
        'scope_id': draft['scope_id'],
        'version': version,
        'site': draft['site'],
        'source': draft['source'],
        'run_id': run_id,
        'rule_version': draft['rule_version'],
        'issued_at': issued_at,
        'mode': mode,
        'state': '已签发',
        'note': note,
        'closed': draft['closed'],
        'coverage': draft['totals'],
        'policy': draft['policy'],
        'devices': draft['devices'],
        'blockers': draft['blockers'],
        'quarantine_ids': draft['quarantine_ids'],
        'basis': {'rule_version': basis['rule_version'],
                  'admission_hash': basis['admission_hash'],
                  'mapping_hash': basis['mapping_hash'],
                  'params_hash': basis['params_hash'],
                  'source': basis['source']},
        'evidence': {'fingerprint': basis['fingerprint'],
                     'admission_hash': basis['admission_hash'],
                     'mapping_hash': basis['mapping_hash'],
                     'params_hash': basis['params_hash'],
                     'source_sha256': basis['source_sha256'],
                     'device_mapping': basis['device_mapping']},
    }
