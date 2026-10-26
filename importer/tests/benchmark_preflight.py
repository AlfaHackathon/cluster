import csv
import json
import resource
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from uuid import UUID
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate
from test_validate import PreflightTests

start = time.monotonic()
fixture = PreflightTests()
fixture.setUp()
root = fixture.root
manifest = fixture.manifest
n = 1380585
def uid(value):
    return str(UUID(int=value + 1))
def replace(role, values, count):
    spec = manifest['files'][role]
    with (root / spec['path']).open('w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(spec['headers'])
        writer.writerows(values)
    spec['rows'] = count
    spec['sha256'] = validate.sha256(root / spec['path'])
replace('companies', ([uid(i)] + [''] * 19 + ['', uid(300000+i % 18098), '', ''] for i in range(204708)), 204708)
replace('persons', ([uid(400000+i)] for i in range(11120)), 11120)
replace('accounts', ([uid(500000+i), uid(400000+i % 11120), uid(i)] for i in range(12514)), 12514)
replace('links', ([uid(400000+i % 11120), '', '', uid(i)] for i in range(12514)), 12514)
replace('payments', ([uid(i % 204708), uid(i // 204708), '0.00000000000000000000000000001', '1'] for i in range(n)), n)
manifest['expected'] = dict(groups=18098, payment_amount='0.00000000000000000000001380585', payment_count=n,
                            payment_duplicate_pairs=0, payment_extra_pair_rows=0, payment_self_rows=7)
result = validate.validate(root, manifest)
print(json.dumps(dict(status=result['status'], rows=result['rows'], elapsed_seconds=round(time.monotonic()-start, 2),
                      peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1))))
fixture.temp.cleanup()
