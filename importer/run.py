#!/usr/bin/env python3
"""Validate offline, then admit bounded batches through internal owner contracts."""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import validate

ORDER = (("company", "groups"), ("company", "companies"), ("company", "persons"),
         ("user", "accounts"), ("company", "links"), ("user", "links"), ("company", "payments"))


class Blocked(Exception):
    def __init__(self, code, owner=None, phase=None):
        self.report = dict(status="blocked", code=code, owner=owner, phase=phase,
                           complete=False, skipped=0, persisted="owner_checkpoints_only")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Transport:
    def __init__(self, addresses, secret, sleeper=time.sleep):
        if not secret:
            raise Blocked("internal_secret_missing")
        self.addresses, self.secret, self.sleep = addresses, secret, sleeper
        for address in addresses.values():
            parsed = urlparse(address)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise Blocked("invalid_owner_address")
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def call(self, owner, path, body=None):
        encoded = None if body is None else json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode()
        for attempt in range(3):
            request = Request(self.addresses[owner].rstrip("/") + "/" + path, data=encoded,
                              headers={"X-Internal-Secret": self.secret, "Content-Type": "application/json"})
            try:
                with self.opener.open(request, timeout=120) as response:
                    raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise Blocked("owner_response_limit", owner)
                return json.loads(raw)
            except HTTPError as error:
                # Never log owner validation bodies: a framework error may contain a source value.
                if error.code not in (502, 503, 504) or attempt == 2:
                    raise Blocked("owner_http_" + str(error.code), owner) from None
            except (URLError, TimeoutError, OSError):
                if attempt == 2:
                    raise Blocked("owner_outcome_uncertain_resume_from_checkpoint", owner) from None
            except (ValueError, UnicodeError):
                raise Blocked("invalid_owner_response", owner) from None
            self.sleep(2 ** attempt)
        raise Blocked("owner_unavailable", owner)


def envelope(manifest, manifest_json, manifest_hash, phase, start, data):
    return dict(sourceId=manifest["source_id"], versionId=manifest["version"], manifestHash=manifest_hash,
                manifestJson=manifest_json, phase=phase, startRow=start, rows=data)


def batches(directory, manifest, phase, start, maximum):
    spec = manifest["files"][phase]
    current, pairs = [], set()
    for number, fields in validate.rows(directory, phase, spec):
        if number < start:
            continue
        pair = None
        if phase == "links":
            columns = spec["columns"]
            pair = (fields[columns["person_id"]], fields[columns["company_id"]])
        if current and (len(current) >= maximum or pair is not None and pair in pairs):
            yield current
            current, pairs = [], set()
        current.append(dict(number=number, values=[fields[header] for header in spec["headers"]]))
        if pair is not None:
            pairs.add(pair)
    if current:
        yield current


def apply(directory, manifest, transport, stop_after=None, batch_size=100, progress=None):
    # The exact ASCII JSON string is hashed and bound by both owners. It includes every file hash.
    manifest_json = json.dumps(manifest, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    manifest_hash = validate.hashlib.sha256(manifest_json.encode()).hexdigest()
    scope = urlencode(dict(sourceId=manifest["source_id"], versionId=manifest["version"], manifestHash=manifest_hash))
    verified = []
    for owner, phase in ORDER:
        try:
            ready = transport.call(owner, "readiness")
            if ready.get("contract") != 1 or ready.get("enabled") is not True:
                raise Blocked("owner_contract_not_ready", owner, phase)
            checkpoint = transport.call(owner, "checkpoint?" + scope + "&phase=" + phase)
            start = checkpoint.get("nextRow")
            count = manifest["files"][phase]["rows"]
            if type(start) is not int or not 1 <= start <= count + 1 or checkpoint.get("manifestHash") != manifest_hash:
                raise Blocked("invalid_owner_checkpoint", owner, phase)
            for data in batches(directory, manifest, phase, start, 500 if phase == "payments" else batch_size):
                body = envelope(manifest, manifest_json, manifest_hash, phase, data[0]["number"], data)
                receipt = transport.call(owner, "batches", body)
                expected = dict(sourceId=manifest["source_id"], versionId=manifest["version"], manifestHash=manifest_hash,
                                phase=phase, startRow=data[0]["number"], endRow=data[-1]["number"], rows=len(data))
                if any(receipt.get(key) != value for key, value in expected.items()) or receipt.get("skipped") != 0 or receipt.get("conflicts") != 0:
                    raise Blocked("owner_batch_receipt_mismatch", owner, phase)
            result = transport.call(owner, "verify", envelope(manifest, manifest_json, manifest_hash, phase, 1, []))
            if result.get("verified") is not True or result.get("manifestHash") != manifest_hash or result.get("phase") != phase:
                raise Blocked("owner_verification_not_confirmed", owner, phase)
            verified.append(dict(owner=owner, phase=phase, rows=count))
            if progress:
                progress(dict(status="phase_verified", owner=owner, phase=phase, rows=count, skipped=0, conflicts=0))
            if stop_after == phase:
                return dict(status="stopped_after_verified_phase", complete=False, manifestHash=manifest_hash, verified=verified)
        except Blocked as error:
            error.report.update(owner=owner, phase=phase, verified_phases=verified)
            raise
    # Done is only an aggregate of independently verified owner commits; no local marker is trusted.
    return dict(status="done", complete=True, manifestHash=manifest_hash, verified=verified, skipped=0, conflicts=0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--mode", choices=("dry-run", "apply"), default="dry-run")
    parser.add_argument("--stop-after", choices=("groups", "companies", "persons", "accounts"))
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        validate.require(1 <= args.batch_size <= 100, "batch_size")
        manifest = validate.load_manifest(args.manifest)
        preflight = validate.validate(args.input, manifest)
        if args.mode == "dry-run":
            print(json.dumps(preflight, sort_keys=True))
            return 0
        transport = Transport({"company": os.environ.get("COMPANY_IMPORT_URL", ""),
                               "user": os.environ.get("USER_IMPORT_URL", "")}, os.environ.get("INTERNAL_SECRET", ""))
        result = apply(args.input, manifest, transport, args.stop_after, args.batch_size,
                       lambda event: print(json.dumps(event), file=sys.stderr, flush=True))
        result["statistics"] = preflight["statistics"]
        result["statistics"]["persisted"] = {
            item["owner"] + ":" + item["phase"]: item["rows"] for item in result["verified"]}
        print(json.dumps(result, sort_keys=True))
        return 0
    except validate.Invalid as error:
        print(json.dumps(validate.failure_report(error)), file=sys.stderr)
        return 2
    except Blocked as error:
        print(json.dumps(error.report), file=sys.stderr)
        return 3
    except OSError:
        print(json.dumps(validate.failure_report(validate.Invalid("input_io"))), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
