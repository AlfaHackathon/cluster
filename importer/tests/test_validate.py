import contextlib
import csv
import hashlib
import io
import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import validate as subject

C1 = "11111111-1111-4111-8111-111111111111"
C2 = "22222222-2222-4222-8222-222222222222"
P = "33333333-3333-4333-8333-333333333333"
A = "44444444-4444-4444-8444-444444444444"
G = "55555555-5555-4555-8555-555555555555"
COMPANY = "COMPANY_ID,ORG_TYPE,OKFS_NAME,COMPANY_AGE_YEARS_GRADATION,ECO_MARK_LABEL,MAINOKVED_CODE,MAINOKVED_NAME,NEGATIVEMARK,AMOUNT_WORKERS_FNS_GRADATION,AMOUNT_WORKERS_SPARK_GRADATION,CL_TYPE,CL_PRODUCT_RKO_MARK,CL_PRODUCT_GAR_MARK,CL_PRODUCT_LOA_MARK,CL_PRODUCT_LEA_MARK,CL_PRODUCT_DEP_MARK,CL_PRODUCT_ZPP_MARK,FTS_FLAG,FTS_COUNTRY_NAME,FACTORING_MARK,REVENUE_COMPANY,GROUP_ID,CITY,REGION,PROFILE_INCOMPLETE".split(",")


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = {
            "groups": [[G]],
            "companies": [[C1] + [""] * 19 + ["  ", G, "", "", "1"],
                          [C2] + [""] * 19 + ["-0001.2300", "", "", "", "0"]],
            "persons": [[P]], "accounts": [[A, P, C1]],
            "links": [[P, "  raw;city\n", "", C1]],
            "payments": [[C2, C1, "+000.10000000000000000000000000001", "0001"],
                         [C2, C1, "-0.1", "2"], [C1, C1, "5", "0"]],
        }
        headers = {"groups": ["GROUP_ID"], "companies": COMPANY, "persons": ["person_uuid"],
                   "accounts": ["account_uuid", "person_uuid", "company_uuid"],
                   "links": ["SYNTHETIC_F", "CITY_NAME", "RC_NAME", "SYNTHETIC_U"],
                   "payments": ["BENEFICIARY", "PAYER", "PAY_AMT", "PAY_CNT"]}
        mappings = {
            "groups": dict(group_id="GROUP_ID"),
            "companies": dict(company_id="COMPANY_ID", group_id="GROUP_ID", revenue="REVENUE_COMPANY"),
            "persons": dict(person_id="person_uuid"),
            "accounts": dict(account_id="account_uuid", person_id="person_uuid", company_id="company_uuid"),
            "links": dict(person_id="SYNTHETIC_F", company_id="SYNTHETIC_U", city_name="CITY_NAME", rc_name="RC_NAME"),
            "payments": dict(beneficiary="BENEFICIARY", payer="PAYER", amount="PAY_AMT", count="PAY_CNT"),
        }
        self.manifest = {"format": 1, "source_id": "synthetic-only", "version": "fixture-v1", "files": {},
                         "expected": dict(groups=1, payment_amount="5.00000000000000000000000000001",
                                          payment_count=3, payment_duplicate_pairs=1,
                                          payment_extra_pair_rows=1, payment_self_rows=1)}
        for role in subject.ROLES:
            self.manifest["files"][role] = dict(path=role + ".csv", sha256="0" * 64,
                                                delimiter=";", headers=headers[role], columns=mappings[role],
                                                rows=len(self.data[role]))
            self.write(role)

    def write(self, role):
        spec = self.manifest["files"][role]
        with (self.root / spec["path"]).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, delimiter=spec["delimiter"])
            writer.writerow(spec["headers"])
            writer.writerows(self.data[role])
        spec["sha256"] = subject.sha256(self.root / spec["path"])
        spec["rows"] = len(self.data[role])

    def manifest_path(self):
        path = self.root / "manifest.json"
        path.write_text(json.dumps(self.manifest), encoding="utf-8")
        return path

    def check(self):
        return subject.validate(self.root, subject.load_manifest(self.manifest_path()))

    def error(self, code):
        with self.assertRaises(subject.Invalid) as caught:
            self.check()
        self.assertEqual(caught.exception.diagnostic["code"], code)
        return caught.exception.diagnostic

    def test_exact_totals_duplicate_pairs_self_rows_and_repeat_identity(self):
        first = self.check()
        self.assertEqual(first, self.check())
        self.assertEqual(first["verified"]["payment_amount"], "5.00000000000000000000000000001")
        self.assertEqual(first["verified"]["payment_duplicate_pairs"], 1)
        self.assertEqual(first["self_payment_amount"], "5")
        self.assertEqual(first["rows"]["payments"], 3)

    def test_batch_identity_changes_with_version_or_manifest(self):
        first = self.check()["batch_id"]
        self.manifest["version"] = "fixture-v2"
        self.assertNotEqual(first, self.check()["batch_id"])

    def test_explicit_statistics_do_not_skip_incomplete_rows(self):
        report = self.check()
        stats = report["statistics"]
        self.assertEqual(stats["skipped"], 0)
        self.assertEqual(stats["conflicts"], 0)
        self.assertEqual(stats["persisted"], 0)
        self.assertEqual(stats["incomplete"], dict(groups=0, companies=2, persons=0, accounts=0, links=1, payments=0))
        self.assertEqual(report["rows"]["companies"], 2)

    def test_error_statistics_are_explicitly_partial(self):
        report = subject.failure_report(subject.Invalid("duplicate_account_or_pair", "accounts", 2))
        self.assertEqual(report["statistics"]["conflicts"], 1)
        self.assertEqual(report["statistics"]["skipped"], 0)
        self.assertIsNone(report["statistics"]["incomplete"])
        self.assertEqual(report["statistics"]["scope"], "stopped_at_first_error_not_full_dataset")

    def test_input_hash_mismatch(self):
        (self.root / "payments.csv").write_text("sensitive-not-logged")
        self.error("source_hash_mismatch")

    def test_exact_headers_and_no_header_guessing(self):
        self.manifest["files"]["persons"]["headers"] = ["PERSON"]
        self.manifest["files"]["persons"]["columns"] = {"person_id": "PERSON"}
        self.error("headers_mismatch")

    def test_fk_missing_incomplete_companies_are_not_inferred(self):
        self.data["payments"][0][0] = G
        self.write("payments")
        self.error("foreign_key")

    def test_duplicate_company(self):
        self.data["companies"][1][0] = C1
        self.write("companies")
        self.error("duplicate_company")

    def test_duplicate_person(self):
        self.data["persons"].append([P])
        self.write("persons")
        self.error("duplicate_person")

    def test_pair_cannot_have_two_accounts(self):
        self.data["accounts"].append([G, P, C1])
        self.write("accounts")
        self.error("duplicate_account_or_pair")

    def test_one_person_can_have_accounts_in_two_companies(self):
        self.data["accounts"].append([G, P, C2])
        self.data["links"].append([P, "", "", C2])
        self.write("accounts")
        self.write("links")
        self.assertEqual(self.check()["rows"]["accounts"], 2)

    def test_links_need_exact_pair_coverage(self):
        self.data["links"] = []
        self.write("links")
        self.error("account_link_coverage")

    def test_link_does_not_create_account(self):
        self.data["links"][0][-1] = C2
        self.write("links")
        self.error("missing_account_pair")

    def test_geography_utf16_bound(self):
        self.data["links"][0][1] = "😀" * 8001
        self.write("links")
        self.error("geography_too_long")

    def test_payment_lexical_rejections(self):
        for raw in ("NaN", "1e2", "1,2", "1.0\n", "", "1" * 4097):
            with self.subTest(raw_length=len(raw)):
                self.data["payments"][0][2] = raw
                self.write("payments")
                self.error("invalid_payment_amount")

    def test_count_rejects_sign_and_fraction(self):
        for raw in ("-1", "+1", "1.0", "1\n"):
            self.data["payments"][0][3] = raw
            self.write("payments")
            self.error("invalid_payment_count")

    def test_revenue_opaque_unknown_or_exact_plain_decimal(self):
        self.data["companies"][0][20] = "1e6"
        self.write("companies")
        self.error("invalid_revenue")

    def test_revenue_whitespace_matches_dotnet(self):
        self.data["companies"][0][20] = "\u001c"
        self.write("companies")
        self.error("invalid_revenue")
        self.data["companies"][0][20] = "\u0085\u2000\u2028"
        self.write("companies")
        self.assertEqual(self.check()["status"], "validated")

    def test_unicode_controls_rejected_in_source_identity(self):
        self.manifest["source_id"] = "a\u0085b"
        self.error("manifest_identity")

    def test_manifest_identity_utf16_bound_and_surrogates(self):
        self.manifest["source_id"] = "😀" * 100
        self.assertEqual(self.check()["status"], "validated")
        self.manifest["source_id"] = "😀" * 101
        self.error("manifest_identity")
        self.manifest["source_id"] = "\ud800"
        self.error("manifest_identity")

    def test_nul_is_rejected_without_cell_in_error(self):
        self.data["links"][0][1] = "private-name\0"
        self.write("links")
        diagnostic = self.error("nul_cell")
        self.assertEqual(set(diagnostic), {"code", "role", "row"})
        self.assertNotIn("private-name", json.dumps(diagnostic))

    def test_row_count_and_totals_fail(self):
        self.manifest["files"]["payments"]["rows"] += 1
        self.error("row_count_mismatch")
        self.manifest["files"]["payments"]["rows"] -= 1
        self.manifest["expected"]["payment_amount"] = "5"
        self.error("verification_payment_amount")

    def test_noncanonical_or_empty_uuid(self):
        for raw in ("00000000-0000-0000-0000-000000000000", "invalid", "{" + C1 + "}"):
            self.data["companies"][0][0] = raw
            self.write("companies")
            with self.assertRaises(subject.Invalid):
                self.check()

    def test_paths_cannot_escape_readonly_mount(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "outside.csv"
            outside.write_text("private")
            (self.root / "escape.csv").symlink_to(outside)
            self.manifest["files"]["persons"]["path"] = "escape.csv"
            self.error("input_outside_mount")

    def test_apply_is_explicitly_blocked_without_success_marker(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = subject.main(["--input", str(self.root), "--manifest", str(self.manifest_path()), "--mode", "apply"])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output.getvalue())["status"], "blocked")
        self.assertEqual(json.loads(output.getvalue())["mode"], "apply")
        self.assertFalse(any("done" in path.name for path in self.root.iterdir()))

    def test_invalid_manifest_errors_do_not_echo_values(self):
        path = self.manifest_path()
        path.write_text("sensitive-invalid-json")
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            code = subject.main(["--input", str(self.root), "--manifest", str(path)])
        self.assertEqual(code, 2)
        self.assertNotIn("sensitive", output.getvalue())


if __name__ == "__main__":
    unittest.main()
