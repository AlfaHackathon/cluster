"""Populate and compare canonical question/dependency/validity state across migration."""
import argparse
import json
import os
from pathlib import Path
import urllib.request
import urllib.error

COMPANY = '463c7bcd-f752-44c8-9dbd-134bf55529d4'
BEFORE = '2026-10-22T12:00:00.1234566Z'
AT = '2026-10-22T12:00:00.1234567Z'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=('initial', 'restart'), required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    events = []
    result = {'status': 'failed', 'phase': args.phase, 'events': events}

    def call(service, path, body=None):
        request = urllib.request.Request(os.environ[f'PROOF_{service.upper()}_URL'] + f'/{service}-service/internal' + path,
                    data=None if body is None else json.dumps(body).encode(),
                    headers={'Content-Type': 'application/json', 'X-Internal-Secret': os.environ['NEWS_TRACER_INTERNAL_SECRET']})
        try:
            response = urllib.request.urlopen(request, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            data = json.load(response)
            events.append({'service': service, 'path': path, 'status': response.status, 'response': data})
            assert response.status == 200, (path, response.status, data)
            return data

    snapshot_path = '/effects?companyId=' + COMPANY + '&newsMaterialId=canonical-news&asOf='

    def state(effect_id):
        return {'effect': call('insight', '/effects/' + effect_id),
                'before': call('insight', snapshot_path + BEFORE),
                'at': call('insight', snapshot_path + AT),
                'recipients': call('insight', '/question-recipients?asOf=' + AT + '&materialId=canonical-contract')}

    try:
        if args.phase == 'initial':
            refs = []
            for material_id, kind in (('canonical-news', 'news'), ('canonical-contract', 'company')):
                saved = call('context', '/material-versions', {
                    'materialId': material_id, 'versionId': 'v1', 'sourceId': 'synthetic', 'kind': kind,
                    'title': 'Migration fixture', 'text': 'Synthetic contract terms are unknown.',
                    'knownAt': '2026-10-22T10:00:00Z', 'publishedAt': None, 'correctsVersionId': None,
                    'bankProfileIds': [], 'companyIds': [COMPANY] if kind == 'company' else []})
                refs.append({key: saved[key] for key in ('materialId', 'versionId', 'contentHash')})
            request = {'companyId': COMPANY, 'newsMaterialId': 'canonical-news', 'newsVersionId': 'v1',
                       'asOf': '2026-10-22T11:00:00Z', 'drafts': [{
                           'effectKey': 'question-financing', 'literalObjectRef': 'company:' + COMPANY,
                           'conclusion': 'Synthetic fixture: unknown contract terms', 'status': 'unknown',
                           'sourceRefs': refs, 'question': {'mechanism': 'financing_cost', 'frame': 'absolute',
                               'unit': 'RUB', 'targetPeriod': '2026', 'baselinePeriod': None}}]}
            receipt = call('insight', '/analyses', request)
            effect_id = receipt['effects'][0]['effectId']
            change = {'category': 'material:company', 'entityId': 'canonical-contract', 'versionId': 'v2',
                      'knownAt': AT, 'companyIds': [COMPANY]}
            reaction = call('insight', '/source-changes', change)
            assert reaction['items'][0]['effectId'] == effect_id
            assert call('insight', '/source-changes', change) == reaction
            observed = state(effect_id)
            assert observed['effect']['requiresReassessment'] is True
            assert observed['before']['effects'][0]['requiresReassessment'] is False
            assert observed['at']['effects'][0]['requiresReassessment'] is True
            assert COMPANY in observed['recipients']['companyIds']
            (args.out / 'question-state.json').write_text(json.dumps({'request': request, 'receipt': receipt,
                'state': observed, 'effectId': effect_id}, indent=2), encoding='utf-8')
        else:
            old = json.loads((args.out / 'question-state.json').read_text(encoding='utf-8'))
            assert state(old['effectId']) == old['state']
            replay = call('insight', '/analyses', old['request'])
            assert replay['duplicate'] is True
            assert replay['analysisId'] == old['receipt']['analysisId']
            assert replay['effects'][0]['revisionId'] == old['receipt']['effects'][0]['revisionId']
            assert call('insight', '/effects/' + old['effectId']) == old['state']['effect']
        result['status'] = 'pass'
    except Exception as error:
        result['error'] = str(error)
        raise
    finally:
        (args.out / (args.phase + '-question-proof.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'status': result['status'], 'events': len(events)}), flush=True)


if __name__ == '__main__':
    main()
