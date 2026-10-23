"""Real loopback HTTP proof. Synthetic facts; no external model/DB or secrets logged."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


COMPANY = '94ae3113-e339-47b5-b394-7626c0fd0808'
OTHER = '984cbaac-3581-4a69-9080-5ebcf150f4c7'
NEWS = 'local-tariff-news'
CONTRACT = 'local-contract-104'
EARLY = '2026-10-21T07:20:00Z'
LATE = '2026-10-21T09:00:00Z'


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['initial', 'restart'], default='initial')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    secret = os.environ['NEWS_TRACER_INTERNAL_SECRET']
    context_url = os.environ['PROOF_CONTEXT_URL']
    insight_url = os.environ['PROOF_INSIGHT_URL']
    agent_url = os.environ['PROOF_AGENT_URL']
    bases = {'context': context_url + '/context-service/internal',
             'insight': insight_url + '/insight-service/internal',
             'agent': agent_url + '/agent-service/internal',
             'agent_test': agent_url}
    events = []

    def call(service, method, path, body=None, allowed=(200,), authorized=True):
        headers = {'Content-Type': 'application/json'}
        if authorized:
            headers['X-Internal-Secret'] = secret
        request = urllib.request.Request(bases[service] + path,
                    data=compact(body).encode() if body is not None else None,
                    method=method, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=90)
        except urllib.error.HTTPError as error:
            response = error
        status = response.status
        raw = response.read().decode()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {'nonJson': raw[:300]}
        events.append({'service': service, 'method': method, 'path': path,
                       'request': body, 'status': status, 'response': data})
        assert status in allowed, (service, method, path, status, data)
        return data

    def query(**kwargs):
        return '?' + urllib.parse.urlencode(kwargs)

    def material(mid, version, kind, text, known, corrects=None):
        body = {'materialId': mid, 'versionId': version, 'sourceId': mid,
                'kind': kind, 'title': 'SYNTHETIC local HTTP tracer: ' + mid,
                'text': text, 'knownAt': known, 'publishedAt': known,
                'companyIds': [COMPANY], 'bankProfileIds': ['U_96']}
        if corrects:
            body['correctsVersionId'] = corrects
        return call('context', 'POST', '/material-versions', body, (200, 201))

    def version(mid, version):
        return call('context', 'GET', '/material-versions/' + mid + '/' + version)

    def ref(row):
        return {k: row[k] for k in ('materialId', 'versionId', 'contentHash')}

    def job(version_id, as_of=LATE):
        return {'companyId': COMPANY, 'bankProfileId': 'U_96',
                'newsMaterialId': NEWS, 'newsVersionId': version_id, 'asOf': as_of}

    def receipt(version_id, allowed=(200,)):
        return call('insight', 'GET', '/analyses' + query(companyId=COMPANY,
                    newsMaterialId=NEWS, newsVersionId=version_id), allowed=allowed)

    def effects():
        return call('insight', 'GET', '/effects' + query(companyId=COMPANY,
                                                     newsMaterialId=NEWS))['effects']

    def value(row):
        # Agreed wire result is owned/calculated by InsightService.
        calculation = row['currentRevision']['calculationResult']
        return Decimal(str(calculation))

    state_path = out / 'initial-state.json'
    try:
        if args.phase == 'restart':
            old = json.loads(state_path.read_text(encoding='utf-8'))
            current = effects()
            assert len(current) == 1 and current[0]['effectId'] == old['effectId']
            detail = call('insight', 'GET', '/effects/' + old['effectId'])
            assert len(detail['history']) == 2
            assert value(current[0]) == Decimal('6240')
            assert receipt('v1')['effects'][0]['revisionId'] == old['firstRevisionId']
            assert receipt('v2')['effects'][0]['revisionId'] == old['secondRevisionId']
            assert receipt('v3')['effects'] == []
            assert version(CONTRACT, 'v1')['contentHash'] == old['contractHash']
            repeated = call('agent', 'POST', '/news-analysis', job('v2'))
            assert repeated['duplicate'] is True
            assert call('agent_test', 'GET', '/__test/reasoning-calls')['calls'] == 0
        else:
            for service, path in [('context', '/context'), ('insight', '/analyses'),
                                   ('agent', '/news-analysis')]:
                call(service, 'POST', path, {}, (401, 403), authorized=False)
            text = ('SYNTHETIC. Company U_96, contract LOT-104: 104 service units, '
                    'baseline 300 RUB/unit; no VAT/add-ons. Planned acceptance. '
                    'Only this company is payer. No act or payment exists.')
            contract = material(CONTRACT, 'v1', 'company', text,
                                '2026-10-21T07:00:00Z')
            first = material(NEWS, 'v1', 'news',
                    'SYNTHETIC. Service tariff rises from 300 to 450 RUB per unit '
                    'for the planned LOT-104. Forecast additional expense only.',
                    '2026-10-21T07:10:00Z')
            correction = material(NEWS, 'v2', 'news',
                    'SYNTHETIC CORRECTION of v1: revised tariff is 360 RUB per '
                    'unit, not 450. Same LOT-104, same 104 planned units. '
                    'No act or payment. Update the existing effect.',
                    '2026-10-21T07:30:00Z', 'v1')
            same = material(CONTRACT, 'v1', 'company', text,
                            '2026-10-21T07:00:00Z')
            assert same['contentHash'] == contract['contentHash']
            changed = {k: v for k, v in contract.items() if k != 'contentHash'}
            changed['text'] = text + ' conflicting mutation'
            call('context', 'POST', '/material-versions', changed, (409,))
            call('context', 'POST', '/context', {k: v for k, v in job('v2', EARLY).items()
                  if k != 'bankProfileId'}, (400, 403, 404, 422))
            other = call('context', 'POST', '/context',
                    {**{k: v for k, v in job('v1').items() if k != 'bankProfileId'},
                     'companyId': OTHER}, allowed=(403,))
            assert other['code'] == 'company_scope'
            initial = call('agent', 'POST', '/news-analysis', job('v1', EARLY))
            assert len(initial['effects']) == 1
            assert value(initial['effects'][0]) == Decimal('15600')
            replay = call('agent', 'POST', '/news-analysis', job('v1', EARLY))
            assert replay['duplicate'] is True
            updated = call('agent', 'POST', '/news-analysis', job('v2'))
            assert len(updated['effects']) == 1
            a, b = initial['effects'][0], updated['effects'][0]
            assert a['effectId'] == b['effectId'] and a['revisionId'] != b['revisionId']
            assert b['previousRevisionId'] == a['revisionId']
            assert value(b) == Decimal('6240')
            late_replay = call('agent', 'POST', '/news-analysis', job('v1', EARLY))
            assert late_replay['effects'][0]['revisionId'] == a['revisionId']
            current = effects()
            assert len(current) == 1 and current[0]['currentRevision']['revisionId'] == b['revisionId']
            detail = call('insight', 'GET', '/effects/' + a['effectId'])
            assert len(detail['history']) == 2
            third = material(NEWS, 'v3', 'news',
                    'SYNTHETIC third publication for failure/idempotency tests only.',
                    '2026-10-21T08:00:00Z', 'v2')
            sources = [ref(third), ref(contract)]
            prior = b['revisionId']
            draft = {k: v for k, v in b['currentRevision'].items()
                     if k in ('conclusion', 'status', 'calculation')}
            draft.update(effectKey=current[0]['effectKey'],
                         literalObjectRef=current[0]['literalObjectRef'])
            draft.update(expectedPreviousRevisionId=prior, sourceRefs=sources)
            stale = {**draft, 'expectedPreviousRevisionId': a['revisionId']}
            call('insight', 'POST', '/analyses', {**job('v3'), 'drafts': [stale]}, (409,))
            bad = {**draft, 'effectKey': 'invalid-new-effect',
                   'expectedPreviousRevisionId': None,
                   'sourceRefs': [ref(third), {**ref(contract), 'contentHash': '0' * 64}]}
            good_new = {**draft, 'effectKey': 'must-not-partially-commit',
                        'expectedPreviousRevisionId': None}
            call('insight', 'POST', '/analyses',
                 {**job('v3'), 'drafts': [good_new, bad]}, (400, 409, 422))
            assert len(effects()) == 1
            receipt('v3', (404,))
            empty = call('insight', 'POST', '/analyses', {**job('v3'), 'drafts': []})
            assert empty['effects'] == []
            empty_replay = call('agent', 'POST', '/news-analysis', job('v3'))
            assert empty_replay['duplicate'] is True and empty_replay['effects'] == []
            assert call('agent_test', 'GET', '/__test/reasoning-calls')['calls'] == 2
            state = {'effectId': a['effectId'], 'firstRevisionId': a['revisionId'],
                     'secondRevisionId': b['revisionId'], 'contractHash': contract['contentHash']}
            state_path.write_text(json.dumps(state, indent=2), encoding='utf-8')
        outcome = {'status': 'pass', 'phase': args.phase,
                   'scope': 'Real local HTTP + durable source/effect state; '
                            'controlled reasoning fixture, no semantic generalisation',
                   'recorded_at': datetime.now(timezone.utc).isoformat(),
                   'http_events': events}
    except Exception as error:
        outcome = {'status': 'failed', 'phase': args.phase,
                   'error': type(error).__name__ + ': ' + str(error), 'http_events': events}
        raise
    finally:
        target = out / (args.phase + '-http-proof.json')
        if 'outcome' in locals():
            target.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding='utf-8')
            print(compact({'status': outcome['status'], 'phase': args.phase,
                           'events': len(events), 'artifact': str(target)}))


if __name__ == '__main__':
    main()
