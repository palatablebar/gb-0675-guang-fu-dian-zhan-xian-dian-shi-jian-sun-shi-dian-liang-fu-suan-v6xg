from app.certificate import build_certificate


def apply(state):
    # 证书草稿随批计算；签发/失效是独立动作，落在 store 的证书表里。
    draft = state.get('certificate_draft') or build_certificate(state)
    state['certificate_draft'] = draft
    rows = []
    for device in draft['devices']:
        for group, label in (('usable', '可用'), ('blocked', '受阻')):
            for entry in device[group]:
                rows.append({
                    'device': device['device'],
                    'start': entry['start'],
                    'end': entry['end'],
                    'seconds': entry['seconds'],
                    'irradiance_bin': entry['irradiance_bin'],
                    'baseline_kw': entry.get('baseline_kw'),
                    'reference_ids': entry.get('reference_ids') or [],
                    'code': label if group == 'usable' else entry.get('code'),
                    'reason': entry.get('reason', ''),
                })
    state['views']['certificate'] = {
        'title': '基线适用性',
        'rows': rows,
        'metrics': {
            '目标设备': draft['totals']['targets'],
            '可用秒': draft['totals']['usable_seconds'],
            '受阻秒': draft['totals']['blocked_seconds'],
            '覆盖率': f"{draft['totals']['coverage'] * 100:.1f}%",
        },
        'draft': draft,
    }
