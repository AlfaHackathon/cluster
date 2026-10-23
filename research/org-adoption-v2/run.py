"""Published MVC/PostgreSQL contract and SQLite migration proof, synthetic data only.

Requires .NET 10, Node 24, Python stdlib, and an isolated PostgreSQL reachable
through Windows Npgsql and WSL psql. Never invokes an LLM or existing workers.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SERVICES = ('ContextService', 'InsightService')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--nuget-config', type=Path)
    parser.add_argument('--packages', type=Path, help='Dedicated package cache produced by organization source build')
    parser.add_argument('--package-manifest', type=Path, help='Verified source-build provenance.json or latest.json')
    parser.add_argument('--postgres-port', type=int, default=55441)
    args = parser.parse_args()
    run = HERE / 'runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run.mkdir(parents=True)
    creation = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    ports = [free_port() for _ in range(3)]
    assert len(set(ports)) == 3
    urls = [f'http://127.0.0.1:{p}' for p in ports]
    secret = secrets.token_urlsafe(32)
    env = {**os.environ, 'NEWS_TRACER_INTERNAL_SECRET': secret, 'INTERNAL_SECRET': secret,
           'Internal__Secret': secret, 'ContextService__InternalSecret': secret,
           'ContextService__BaseUrl': urls[0], 'Qdrant__WorkerEnabled': 'false',
           'ASPNETCORE_ENVIRONMENT': 'Production', 'DOTNET_ENVIRONMENT': 'Production',
           'CONTEXT_SERVICE_URL': urls[0] + '/context-service',
           'INSIGHT_SERVICE_URL': urls[1] + '/insight-service', 'PORT': str(ports[2]),
           'PROOF_CONTEXT_URL': urls[0], 'PROOF_INSIGHT_URL': urls[1], 'PROOF_AGENT_URL': urls[2]}
    stages, processes, handles, databases = [], [], [], []

    def command(argv, log, extra=None, expected=0):
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env={**env, **(extra or {})}, creationflags=creation, timeout=240)
        (run / log).write_bytes(result.stdout)
        assert result.returncode == expected, f'Exit {result.returncode}, expected {expected}: {log}'
        return result.stdout.decode('utf-8-sig', errors='replace')

    def publish(label, source):
        for service in SERVICES:
            argv = ['dotnet', 'publish', str(source / service / 'Api' / (service + '.Api.csproj')),
                    '-c', 'Release', '-o', str(run / 'published' / label / service), '--nologo']
            if label == 'candidate' and args.nuget_config:
                argv.extend(['--configfile', str(args.nuget_config.resolve())])
            if label == 'candidate' and args.packages:
                argv.append('-p:RestorePackagesPath=' + str(args.packages.resolve()))
            command(argv, f'publish-{label}-{service}.log')

    def package_provenance():
        found = []
        manifest = json.loads(args.package_manifest.read_text(encoding='utf-8-sig')) if args.package_manifest else None
        expected = {f"{p['package']}/{p['version']}".lower(): p for p in manifest['packages']} if manifest else {}
        for service in SERVICES:
            assets = json.loads((ROOT / service / 'Api/obj/project.assets.json').read_text(encoding='utf-8-sig'))
            for package in assets['libraries']:
                if not package.startswith('AlfaHackaton.'):
                    continue
                metadata = next((Path(folder) / package.lower() / '.nupkg.metadata'
                                 for folder in assets['packageFolders']
                                 if (Path(folder) / package.lower() / '.nupkg.metadata').is_file()), None)
                origin = json.loads(metadata.read_text(encoding='utf-8-sig')).get('source') if metadata else None
                registry = origin == 'https://nuget.pkg.github.com/AlfaHackathon/index.json'
                pinned = expected.get(package.lower())
                archive = metadata.parent / (package.replace('/', '.').lower() + '.nupkg') if metadata else None
                digest = hashlib.sha256(archive.read_bytes()).hexdigest() if archive and archive.is_file() else None
                source_built = bool(pinned and origin and
                                    os.path.normcase(os.path.abspath(origin)) == os.path.normcase(os.path.abspath(manifest['feed']))
                                    and digest == pinned['sha256'])
                found.append({'service': service, 'package': package, 'source': origin, 'sha256': digest,
                              'github_packages': registry, 'source_built': source_built,
                              'verified': source_built if manifest else registry})
            if manifest:
                actual = {p['package'].lower() for p in found if p['service'] == service}
                assert actual == set(expected), f'Organization package set differs from manifest: {service}'
        return found

    def psql(sql, log, database='postgres'):
        return command(['wsl.exe', '-d', 'Ubuntu', '--', 'psql', '-h', '127.0.0.1', '-p',
                        str(args.postgres_port), '-U', 'postgres', '-d', database, '-X',
                        '-v', 'ON_ERROR_STOP=1', '-At', '-c', sql], log)

    def create_databases(label):
        result = {}
        for service in SERVICES:
            name = 'orgproof_' + secrets.token_hex(8) + '_' + service.lower()
            psql('CREATE DATABASE ' + name, f'{label}-create-{service}.log')
            databases.append(name)
            result[service] = f'Host=127.0.0.1;Port={args.postgres_port};Database={name};Username=postgres;Pooling=false'
        return result

    def stop():
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        processes.clear()
        for handle in handles:
            handle.close()
        handles.clear()

    def start(label, folder, stage, connections=None):
        for index, service in enumerate((*SERVICES, 'AgentService')):
            handle = (run / f'{stage}-{service}.log').open('wb')
            handles.append(handle)
            if service == 'AgentService':
                argv = ['node', '--experimental-strip-types', str(ROOT / service / 'tests/helpers/news-tracer-server.ts')]
                extra = {}
            else:
                argv = ['dotnet', str(run / 'published' / label / service / (service + '.Api.dll')), '--urls', urls[index]]
                extra = {'Storage__Path': str(folder / (service + '.sqlite'))} if connections is None else {
                    'ConnectionStrings__DefaultConnection': connections[service],
                    'ConnectionStrings__Postgres': connections[service]}
            processes.append(subprocess.Popen(argv, cwd=folder, env={**env, **extra}, stdout=handle,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=creation))
        deadline, pending = time.monotonic() + 60, set(range(3))
        while pending and time.monotonic() < deadline:
            assert all(p.poll() is None for p in processes), f'A service exited: {stage}'
            for index in list(pending):
                path = '/__test/reasoning-calls' if index == 2 else '/health'
                request = urllib.request.Request(urls[index] + path, headers={'X-Internal-Secret': secret})
                try:
                    with urllib.request.urlopen(request, timeout=1) as response:
                        if response.status == 200:
                            pending.remove(index)
                except OSError:
                    pass
            if pending:
                time.sleep(0.15)
        assert not pending, f'Readiness timeout: {stage}, {pending}'

    def proof(label, folder, phase, stage, connections=None):
        start(label, folder, stage, connections)
        try:
            command([sys.executable, '-B', str(HERE / 'question_proof.py'), '--phase', phase,
                     '--out', str(folder)], f'{stage}-question_proof.py.log')
            for script in ('http_proof.py', 'state_proof.py'):
                command([sys.executable, '-B', str(HERE.parent / 'architecture-refactor-v1' / script),
                         '--phase', phase, '--out', str(folder)], f'{stage}-{script}.log')
            stages.append({'stage': stage, 'phase': phase, 'status': 'pass'})
            print('PASS ' + stage, flush=True)
        finally:
            stop()

    report = {'status': 'failed', 'stages': stages, 'llm_used': False, 'real_kafka_used': False,
              'scope': 'Published MVC APIs, actual organization clients and PostgreSQL, synthetic SQLite import'}
    print('RUN ' + str(run), flush=True)
    try:
        publish('candidate', ROOT)
        report['packages'] = package_provenance()
        report['published_package_origin_verified'] = bool(report['packages']) and all(p['github_packages'] for p in report['packages'])
        report['source_built_package_origin_and_hash_verified'] = bool(report['packages']) and all(p['source_built'] for p in report['packages'])
        assert report['packages'] and all(p['verified'] for p in report['packages']), 'Package origin/hash verification failed'
        publish('baseline', ROOT / '.scratch/org-adoption-baseline')
        command(['dotnet', 'publish', str(ROOT / 'cluster/tools/LegacyStateMigration'), '-c', 'Release',
                 '-o', str(run / 'published/migration'), '--nologo'], 'publish-migration.log')
        upgrade = run / 'upgrade'
        upgrade.mkdir()
        proof('baseline', upgrade, 'initial', 'baseline-initial')
        connections = create_databases('upgrade')
        start('candidate', upgrade, 'schema-create', connections)
        stop()
        importer = ['dotnet', str(run / 'published/migration/LegacyStateMigration.dll')]
        # Force a deferred FK failure at commit, after all rows/checksums were processed.
        # The same target must remain empty and accept the subsequent valid import.
        damaged = upgrade / 'damaged-context.sqlite'
        shutil.copyfile(upgrade / 'ContextService.sqlite', damaged)
        with sqlite3.connect(damaged) as db:
            db.execute("UPDATE material_companies SET material_id='missing-' || material_id")
        command([*importer, 'context', str(damaged), '--apply'], 'migration-rollback.json',
                {'MIGRATION_POSTGRES': connections['ContextService']}, expected=1)
        context_db = next(part[9:] for part in connections['ContextService'].split(';') if part.startswith('Database='))
        count = psql('SELECT (SELECT count(*) FROM material_versions) + (SELECT count(*) FROM material_companies) + '
                     '(SELECT count(*) FROM knowledge_revisions) + (SELECT count(*) FROM context_changes) + '
                     '(SELECT count(*) FROM search_projection)', 'migration-rollback-count.log', context_db)
        assert count.strip() == '0', 'Failed migration partially committed'
        report['failed_import_rolled_back'] = True
        for service in SERVICES:
            source = upgrade / (service + '.sqlite')
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            name = service.removesuffix('Service').lower()
            for mode in ('--apply', '--verify'):
                output = command([*importer, name, str(source), mode], f'migration-{name}-{mode[2:]}.json',
                                 {'MIGRATION_POSTGRES': connections[service]})
                assert json.loads(output)['status'] == 'pass'
            output = command([*importer, name, str(source), '--apply'], f'migration-{name}-refuse-existing.json',
                             {'MIGRATION_POSTGRES': connections[service]}, expected=1)
            assert 'Target must be empty' in output
            assert hashlib.sha256(source.read_bytes()).hexdigest() == digest, 'SQLite source was modified'
        report['migration_verified'] = True
        report['source_files_unchanged'] = True
        report['nonempty_target_rejected'] = True
        proof('candidate', upgrade, 'restart', 'imported-restart', connections)
        fresh = run / 'fresh'
        fresh.mkdir()
        connections = create_databases('fresh')
        proof('candidate', fresh, 'initial', 'candidate-initial', connections)
        proof('candidate', fresh, 'restart', 'candidate-restart', connections)
        report['status'] = 'pass'
    finally:
        stop()
        for name in databases:
            psql('DROP DATABASE ' + name + ' WITH (FORCE)', 'drop-' + name + '.log')
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        (run / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'status': report['status'], 'artifact': str(run / 'result.json')}), flush=True)


if __name__ == '__main__':
    main()
