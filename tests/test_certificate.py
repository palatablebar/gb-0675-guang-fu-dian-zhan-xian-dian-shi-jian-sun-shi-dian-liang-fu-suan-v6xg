import pytest
from app.engine import evaluate
from app.modules.certificate.rules import apply as certificate_view
from app.certificate import build_certificate, build_basis


def _draft(result):
    state = dict(result)
    certificate_view(state)
    return state['certificate_draft']


def test_clean_all_usable_but_segments_not_merged(clean):
    draft = _draft(clean)
    assert draft['closed'] is True
    assert draft['totals'] == {'targets': 2, 'union_seconds': 3000, 'usable_seconds': 3000,
                               'blocked_seconds': 0, 'uncovered_seconds': 0, 'coverage': 1.0}
    inv_a = next(d for d in draft['devices'] if d['device'] == 'INV-A')
    # 三段各自成证，不把 10:00–10:30 合并成一条“全覆盖”。
    assert [u['seconds'] for u in inv_a['usable']] == [600, 600, 600]
    assert [u['start'][11:16] for u in inv_a['usable']] == ['02:00', '02:10', '02:20']


def test_dirty_blocks_are_explicit_and_excluded(sample):
    draft = _draft(sample)
    assert draft['closed'] is False
    inv_a = next(d for d in draft['devices'] if d['device'] == 'INV-A')
    assert inv_a['usable_seconds'] == 600
    assert inv_a['blocked_seconds'] == 1200
    codes = [b['code'] for b in inv_a['blocked']]
    assert codes == ['NO_REFERENCE', 'TARGET_GAP']
    no_ref = inv_a['blocked'][0]
    assert no_ref['required_refs'] == 2 and len(no_ref['available_refs']) == 1
    assert no_ref['expected'] == {'zone': '东区', 'sample_at': '2026-10-15T02:10:00+00:00',
                                  'irradiance_bin': '800–900 W/m²'}
    # 受阻区间不得被相邻区间补齐：可用 + 受阻 = 并集。
    assert inv_a['usable_seconds'] + inv_a['blocked_seconds'] == inv_a['union_seconds'] == 1800


def test_blocked_never_receives_baseline(sample):
    draft = _draft(sample)
    for device in draft['devices']:
        for blocked in device['blocked']:
            assert blocked['baseline_kw'] is None
            assert blocked['loss_kwh'] is None


def test_uncovered_union_seconds_count_as_blocked(clean):
    state = dict(clean)
    state['event_unions'] = [dict(clean['event_unions'][0], seconds=2400, end='2026-10-15T02:40:00+00:00')]
    draft = build_certificate(state)
    inv_a = next(d for d in draft['devices'] if d['device'] == 'INV-A')
    assert inv_a['uncovered_seconds'] == 600
    assert draft['closed'] is False
    assert any('未被切片覆盖' in item for item in draft['blockers'])


def test_bins_reported_per_device(clean):
    draft = _draft(clean)
    bins = next(d for d in draft['devices'] if d['device'] == 'INV-A')['bins']
    assert bins == [{'irradiance_bin': '800–900 W/m²', 'usable_seconds': 1800, 'blocked_seconds': 0}]


def test_fingerprint_changes_with_admission(clean, sample):
    assert build_basis(clean)['fingerprint'] != build_basis(sample)['fingerprint']
    assert build_basis(clean)['scope_id'] == build_basis(sample)['scope_id']


def test_fingerprint_changes_with_mapping_or_params():
    from copy import deepcopy
    clean = evaluate({'dataset': 'clean'})
    mapped = deepcopy(clean)
    mapped['context']['devices']['REF-A1']['capacity_kw'] = '200'
    params = deepcopy(clean)
    params['context']['min_references'] = 3
    base = build_basis(clean)
    assert build_basis(mapped)['mapping_hash'] != base['mapping_hash']
    assert build_basis(params)['params_hash'] != base['params_hash']
    assert build_basis(mapped)['fingerprint'] != base['fingerprint']
