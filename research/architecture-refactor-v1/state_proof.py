"""Public HTTP tests for durable job leases, CAS cursors, provenance and change feed."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

COMPANY = '94ae3113-e339-47b5-b394-7626c0fd0808'
TIME = '2026-10-21T09:00:00Z'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=('initial', 'restart'), required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    bases = {s: os.environ[f'PROOF_{s.upper()}_URL'] + f'/{s}-service/internal'
             for s in ('context', 'insight')}
    events = []
    result = {'status': 'failed', 'phase': args.phase, 'events': events}

    def call(service, method, path, body=None, allowed=(200,)):
        request = urllib.request.Request(bases[service] + path, method=method,
                    data=None if body is None else json.dumps(body).encode(),
                    headers={'Content-Type': 'application/json',
                             'X-Internal-Secret': os.environ['NEWS_TRACER_INTERNAL_SECRET']})
        try:
            response = urllib.request.urlopen(request, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read().decode()
            data = json.loads(raw) if raw else None
            status = response.status
        events.append({'service': service, 'method': method, 'path': path,
                       'status': status, 'response': data})
        assert status in allowed, (path, status, data)
        return status, data

    def get(service, path):
        return call(service, 'GET', path)[1]

    def post(service, path, data, allowed=(200,)):
        return call(service, 'POST', path, data, allowed)[1]

    state_path = args.out / 'boundary-state.json'
    try:
        if args.phase == 'initial':
            # Explicit malformed-body binding must still use the established error envelope.
            error = post('context', '/context', {'companyId': 'not-a-guid'}, (400,))
            assert error['code'] == 'invalid_request'
            error = get('insight', '/effects?companyId=' + COMPANY + '&newsMaterialId=local-tariff-news')
            assert len(error['effects']) == 1

            provenance = {'acquisition': 'uploaded', 'sourceUrl': None, 'retrievedAt': TIME,
                          'textCompleteness': 'excerpt', 'locator': 'synthetic.pdf page 2 OCR'}
            material = {'materialId': 'proof-upload', 'versionId': 'v1', 'sourceId': 'proof',
                        'kind': 'company', 'title': 'Synthetic upload', 'text': 'Synthetic evidence',
                        'knownAt': TIME, 'publishedAt': None, 'correctsVersionId': None,
                        'companyIds': [COMPANY], 'bankProfileIds': ['U_96'], 'provenance': provenance}
            saved = post('context', '/material-versions', material)
            assert saved['provenance']['locator'] == provenance['locator']
            before = get('context', '/changes?after=0')['changes']
            post('context', '/material-versions', material)
            assert get('context', '/changes?after=0')['changes'] == before
            post('context', '/material-versions', {**material, 'provenance': {**provenance, 'textCompleteness': 'full'}}, (409,))
            assert get('context', '/changes?after=0')['changes'] == before

            ref = {k: saved[k] for k in ('materialId', 'versionId', 'contentHash')}
            profile = {'companyId': COMPANY, 'revisionId': 'proof-v1', 'bankProfileId': 'U_96',
                       'name': 'Synthetic company', 'description': 'Synthetic', 'industryCodes': ['62.01'],
                       'exposures': [], 'knownAt': TIME, 'sourceRefs': [ref]}
            post('context', '/companies', profile)
            companies = get('context', '/companies?asOf=2026-10-21T10:00:00Z')['companies']
            assert companies[0]['revisionId'] == 'proof-v1'
            assert get('context', '/companies?asOf=2026-10-21T08:00:00Z')['companies'] == []

            request = {'jobId': 'proof-job', 'newsMaterialId': 'local-tariff-news',
                       'newsVersionId': 'v3', 'asOf': TIME, 'parameters': {'companyBudget': 2}}
            created = post('insight', '/news-jobs', request)
            assert created['status'] == 'queued'
            post('insight', '/news-jobs', {**request, 'newsVersionId': 'v2'}, (409,))
            def claim(worker):
                return post('insight', '/news-jobs/claim', {'workerId': worker, 'jobId': 'proof-job'})
            with ThreadPoolExecutor(max_workers=2) as executor:
                claims = list(executor.map(claim, ('proof-a', 'proof-b')))
            winners = [claim for claim in claims if claim is not None]
            assert len(winners) == 1, claims
            leased = winners[0]
            checkpoint = {'completedCompanyIds': ['a'], 'pendingCompanyIds': ['b'],
                          'signals': [{'status': 'unknown', 'sourceRefs': [ref]}]}
            update = {'workerId': leased['workerId'], 'version': leased['version'],
                      'status': 'partial', 'checkpoint': checkpoint}
            post('insight', '/news-jobs/proof-job', {**update, 'workerId': 'intruder'}, (409,))
            post('insight', '/news-jobs/proof-job', update)
            second = claim('proof-c')
            assert second['checkpoint'] == checkpoint
            post('insight', '/news-jobs/proof-job', update, (409,))
            finished = post('insight', '/news-jobs/proof-job',
                            {**update, 'workerId': 'proof-c', 'version': second['version'], 'status': 'completed'})
            assert post('insight', '/news-jobs', request) == finished

            cursor = get('insight', '/source-cursor')
            def advance(sequence):
                return call('insight', 'POST', '/source-cursor', {**cursor, 'sequence': sequence}, (200, 409))
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(advance, (10, 11)))
            assert sorted(item[0] for item in outcomes) == [200, 409]
            durable_cursor = get('insight', '/source-cursor')
            state = {'job': finished, 'cursor': durable_cursor, 'material': saved, 'companies': companies,
                     'changes': get('context', '/changes?after=0')['changes']}
            state_path.write_text(json.dumps(state, indent=2), encoding='utf-8')
        else:
            old = json.loads(state_path.read_text(encoding='utf-8'))
            assert get('insight', '/news-jobs/proof-job') == old['job']
            assert get('insight', '/source-cursor') == old['cursor']
            assert get('context', '/material-versions/proof-upload/v1') == old['material']
            assert get('context', '/companies?asOf=2026-10-21T10:00:00Z')['companies'] == old['companies']
            assert get('context', '/changes?after=0')['changes'] == old['changes']
            assert post('insight', '/news-jobs/claim', {'workerId': 'restart', 'jobId': 'proof-job'}) is None
        result['status'] = 'pass'
    except Exception as error:
        result['error'] = str(error)
        raise
    finally:
        target = args.out / (args.phase + '-boundary-proof.json')
        target.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'status': result['status'], 'events': len(events), 'artifact': str(target)}), flush=True)


if __name__ == '__main__':
    main()
