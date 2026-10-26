#!/usr/bin/env python3
"""Offline, bounded-memory CSV preflight. Never writes to an owner or calls a provider."""
import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from decimal import Decimal, localcontext
from pathlib import Path
from uuid import UUID

ROLES = ("companies", "persons", "accounts", "links", "payments")
KEYS = {
    "companies": {"company_id", "group_id", "revenue"},
    "persons": {"person_id"},
    "accounts": {"account_id", "person_id", "company_id"},
    "links": {"person_id", "company_id", "city_name", "rc_name"},
    "payments": {"beneficiary", "payer", "amount", "count"},
}
HEX = re.compile(r"[0-9a-f]{64}\Z")
AMOUNT = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?\Z")
REVENUE = re.compile(r"-?[0-9]+(?:\.[0-9]+)?\Z")
COUNT = re.compile(r"[0-9]+\Z")


def owner_whitespace(raw):
    # .NET Char.IsWhiteSpace; Python additionally considers U+001C..U+001F whitespace.
    return all(c in "\t\n\v\f\r\u0085" or unicodedata.category(c) in {"Zs", "Zl", "Zp"}
               for c in raw)


def utf16_units(raw):
    return len(raw.encode("utf-16-le", errors="surrogatepass")) // 2


class Invalid(Exception):
    """Only structural diagnostics, never cell contents or parser exceptions."""

    def __init__(self, code, role=None, row=None):
        self.diagnostic = {"code": code}
        if role is not None:
            self.diagnostic["role"] = role
        if row is not None:
            self.diagnostic["row"] = row
        super().__init__(code)


def require(condition, code, role=None, row=None):
    if not condition:
        raise Invalid(code, role, row)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identifier(raw, role, row):
    try:
        value = UUID(raw)
    except (ValueError, AttributeError):
        raise Invalid("invalid_uuid", role, row) from None
    require(value.int != 0 and str(value) == raw, "noncanonical_uuid", role, row)
    return value


def decimal_amount(raw, role, row):
    require(len(raw) <= 4098 and AMOUNT.fullmatch(raw) is not None
            and sum("0" <= char <= "9" for char in raw) <= 4096,
            "invalid_payment_amount", role, row)
    return Decimal(raw)


def integer_count(raw, role, row):
    require(len(raw) <= 4096 and COUNT.fullmatch(raw) is not None,
            "invalid_payment_count", role, row)
    return int(raw)


def load_manifest(path):
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise Invalid("unreadable_manifest") from None
    require(isinstance(manifest, dict), "invalid_manifest")
    require(set(manifest) == {"format", "source_id", "version", "files", "expected"},
            "manifest_fields")
    require(manifest["format"] == 1, "manifest_format")
    for key in ("source_id", "version"):
        raw = manifest[key]
        require(isinstance(raw, str) and 0 < utf16_units(raw) <= 200 and raw.strip() == raw
                and not any(unicodedata.category(c) in {"Cc", "Cs"} for c in raw), "manifest_identity")
    require(isinstance(manifest["files"], dict) and set(manifest["files"]) == set(ROLES),
            "manifest_roles")
    paths = set()
    for role in ROLES:
        spec = manifest["files"][role]
        require(isinstance(spec, dict) and set(spec) == {
            "path", "sha256", "delimiter", "headers", "columns", "rows"}, "file_spec", role)
        require(isinstance(spec["path"], str) and spec["path"] not in paths, "file_path", role)
        paths.add(spec["path"])
        require(isinstance(spec["sha256"], str) and HEX.fullmatch(spec["sha256"]), "file_hash", role)
        require(spec["delimiter"] in (";", ",", "\t"), "delimiter", role)
        headers = spec["headers"]
        require(isinstance(headers, list) and headers and all(isinstance(h, str) and h for h in headers)
                and len(set(headers)) == len(headers), "header_contract", role)
        columns = spec["columns"]
        require(isinstance(columns, dict) and set(columns) == KEYS[role]
                and all(isinstance(h, str) and h in headers for h in columns.values())
                and len(set(columns.values())) == len(columns), "column_contract", role)
        require(type(spec["rows"]) is int and spec["rows"] >= 0, "expected_rows", role)
    require(len(manifest["files"]["companies"]["headers"]) == 24, "company_header_count")
    require(len(manifest["files"]["payments"]["headers"]) == 4, "payment_header_count")
    expected = manifest["expected"]
    require(isinstance(expected, dict) and set(expected) == {
        "groups", "payment_amount", "payment_count", "payment_duplicate_pairs",
        "payment_extra_pair_rows", "payment_self_rows"}, "expected_fields")
    for key in ("groups", "payment_count", "payment_duplicate_pairs", "payment_extra_pair_rows", "payment_self_rows"):
        require(type(expected[key]) is int and expected[key] >= 0, "expected_count")
    require(isinstance(expected["payment_amount"], str) and AMOUNT.fullmatch(expected["payment_amount"]),
            "expected_amount")
    return manifest


def input_path(directory, name):
    root = directory.resolve(strict=True)
    require(not Path(name).is_absolute(), "absolute_input_path")
    path = (root / name).resolve(strict=True)
    require(path.is_relative_to(root) and path.is_file(), "input_outside_mount")
    return path


def rows(directory, role, spec):
    path = input_path(directory, spec["path"])
    require(sha256(path) == spec["sha256"], "source_hash_mismatch", role)
    count = 0
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, delimiter=spec["delimiter"], strict=True)
            require(next(reader, None) == spec["headers"], "headers_mismatch", role)
            for count, cells in enumerate(reader, 1):
                require(len(cells) == len(spec["headers"]), "row_width", role, count)
                require(all("\0" not in value for value in cells), "nul_cell", role, count)
                yield count, dict(zip(spec["headers"], cells))
    except (csv.Error, UnicodeError):
        raise Invalid("csv_encoding_or_syntax", role, count + 1) from None
    require(count == spec["rows"], "row_count_mismatch", role)
    # A read-only mount is required by Compose; this also detects file replacement during validation.
    require(sha256(path) == spec["sha256"], "source_changed_during_validation", role)


def validate(directory, manifest):
    company_ids, group_ids, person_ids = set(), set(), set()
    accounts, account_ids, linked_pairs = {}, set(), set()
    counts = {}
    pairs = Counter()
    amount, payment_count, self_rows = Decimal(0), 0, 0
    self_amount, self_count = Decimal(0), 0
    # Owner permits 4096 digits, including fractional digits. This exact sum accommodates
    # both a 4096-digit integral and fractional term, plus the CSV row-count carry.
    with localcontext() as context:
        context.prec = 10000
        for role in ROLES:
            spec = manifest["files"][role]
            columns = spec["columns"]
            count = 0
            for count, raw in rows(directory, role, spec):
                values = {key: raw[column] for key, column in columns.items()}
                if role == "companies":
                    company = identifier(values["company_id"], role, count)
                    require(company not in company_ids, "duplicate_company", role, count)
                    company_ids.add(company)
                    if values["group_id"]:
                        group_ids.add(identifier(values["group_id"], role, count))
                    revenue = values["revenue"]
                    require(owner_whitespace(revenue) or REVENUE.fullmatch(revenue), "invalid_revenue", role, count)
                elif role == "persons":
                    person = identifier(values["person_id"], role, count)
                    require(person not in person_ids, "duplicate_person", role, count)
                    person_ids.add(person)
                elif role in ("accounts", "links"):
                    person = identifier(values["person_id"], role, count)
                    company = identifier(values["company_id"], role, count)
                    require(person in person_ids and company in company_ids, "foreign_key", role, count)
                    pair = person, company
                    if role == "accounts":
                        account = identifier(values["account_id"], role, count)
                        require(account not in account_ids and pair not in accounts, "duplicate_account_or_pair", role, count)
                        accounts[pair] = account
                        account_ids.add(account)
                    else:
                        require(pair in accounts, "missing_account_pair", role, count)
                        linked_pairs.add(pair)
                        for key in ("city_name", "rc_name"):
                            require(utf16_units(values[key]) <= 16000,
                                    "geography_too_long", role, count)
                else:
                    payer = identifier(values["payer"], role, count)
                    beneficiary = identifier(values["beneficiary"], role, count)
                    require(payer in company_ids and beneficiary in company_ids, "foreign_key", role, count)
                    value = decimal_amount(values["amount"], role, count)
                    quantity = integer_count(values["count"], role, count)
                    amount += value
                    payment_count += quantity
                    pairs[payer, beneficiary] += 1
                    if payer == beneficiary:
                        self_rows += 1
                        self_amount += value
                        self_count += quantity
            counts[role] = count
        require(linked_pairs == set(accounts), "account_link_coverage")
        actual = {
            "groups": len(group_ids), "payment_amount": str(amount), "payment_count": payment_count,
            "payment_duplicate_pairs": sum(n > 1 for n in pairs.values()),
            "payment_extra_pair_rows": sum(n - 1 for n in pairs.values()), "payment_self_rows": self_rows,
        }
        for key, expected in manifest["expected"].items():
            require((Decimal(actual[key]) == Decimal(expected)) if key == "payment_amount"
                    else actual[key] == expected, "verification_" + key)
    canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    return {
        "status": "validated", "mode": "dry-run", "batch_id": hashlib.sha256(canonical).hexdigest(),
        "source_id": manifest["source_id"], "version": manifest["version"], "rows": counts,
        "verified": actual, "self_payment_amount": str(self_amount), "self_payment_count": self_count,
        "apply_status": "blocked", "blockers": [
            "company_capture_requires_context_material_before_owner_commit",
            "group_person_link_contract_requires_verified_context_evidence",
            "user_candidate_not_deployed",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--mode", choices=("dry-run", "apply"), default="dry-run")
    args = parser.parse_args(argv)
    try:
        report = validate(args.input, load_manifest(args.manifest))
        report["mode"] = args.mode
        if args.mode == "apply":
            report["status"] = "blocked"
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        return 3 if args.mode == "apply" else 0
    except Invalid as error:
        print(json.dumps({"status": "failed", **error.diagnostic}), file=sys.stderr)
        return 2
    except OSError:
        print('{"status":"failed","code":"input_io"}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
