import importlib.util
import unittest
import sys
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run
import validate
import test_validate as fixtures


class FakeOwners:
    """Durable owner receipts simulated across client crashes; no database or sockets."""
    def __init__(self):
        self.receipts = {}
        self.rows = {}
        self.verified = set()
        self.fail_user = False
        self.drop_after_commit = False
        self.phases = []

    def call(self, owner, path, body=None):
        if owner == "user" and self.fail_user:
            raise run.Blocked("owner_http_503", owner)
        if path == "readiness":
            return dict(contract=1, enabled=True)
        if path.startswith("checkpoint?"):
            from urllib.parse import parse_qs
            query = parse_qs(path.split("?", 1)[1])
            phase = query["phase"][0]
            return dict(nextRow=len(self.rows.get((owner, phase), [])) + 1,
                        manifestHash=query["manifestHash"][0], phase=phase)
        phase = body["phase"]
        key = owner, phase
        if path == "verify":
            self.verified.add(key)
            return dict(verified=True, manifestHash=body["manifestHash"], phase=phase)
        assert path == "batches"
        start = body["startRow"]
        replay = owner, phase, start
        if replay not in self.receipts:
            self.rows.setdefault(key, []).extend(body["rows"])
            receipt = {k: body[k] for k in ("sourceId", "versionId", "manifestHash", "phase", "startRow")}
            receipt.update(endRow=body["rows"][-1]["number"], rows=len(body["rows"]), skipped=0, conflicts=0)
            self.receipts[replay] = receipt
            self.phases.append(key)
            if self.drop_after_commit:
                self.drop_after_commit = False
                raise run.Blocked("owner_outcome_uncertain_resume_from_checkpoint", owner, phase)
        return self.receipts[replay]


class WritableClientTests(unittest.TestCase):
    setUp = fixtures.PreflightTests.setUp
    write = fixtures.PreflightTests.write
    def test_resume_after_lost_receipt_uses_owner_checkpoint_without_duplicates(self):
        owners = FakeOwners()
        owners.drop_after_commit = True
        with self.assertRaises(run.Blocked):
            run.apply(self.root, self.manifest, owners, batch_size=1)
        result = run.apply(self.root, self.manifest, owners, batch_size=1)
        self.assertTrue(result["complete"])
        self.assertEqual(len(owners.rows["company", "groups"]), 1)
        self.assertEqual(len(owners.rows["company", "payments"]), 3)
        self.assertEqual(len(owners.verified), 7)

    def test_user_prerequisite_block_preserves_verified_company_prefix(self):
        owners = FakeOwners()
        owners.fail_user = True
        with self.assertRaises(run.Blocked) as caught:
            run.apply(self.root, self.manifest, owners)
        self.assertEqual(caught.exception.report["phase"], "accounts")
        self.assertFalse(caught.exception.report["complete"])
        self.assertEqual({p for o, p in owners.verified}, {"groups", "companies", "persons"})
        owners.fail_user = False
        result = run.apply(self.root, self.manifest, owners)
        self.assertTrue(result["complete"])
        self.assertEqual(len(owners.rows["company", "companies"]), 2)

    def test_no_done_after_verification_failure(self):
        owners = FakeOwners()
        call = owners.call
        def wrong(owner, path, body=None):
            if path == "verify":
                return dict(verified=False)
            return call(owner, path, body)
        owners.call = wrong
        with self.assertRaises(run.Blocked) as caught:
            run.apply(self.root, self.manifest, owners)
        self.assertEqual(caught.exception.report["code"], "owner_verification_not_confirmed")

    def test_explicit_stop_is_not_full_success(self):
        result = run.apply(self.root, self.manifest, FakeOwners(), stop_after="companies")
        self.assertEqual(result["status"], "stopped_after_verified_phase")
        self.assertFalse(result["complete"])

    def test_repeated_link_pairs_remain_separate_rows_and_batches(self):
        self.data["links"].append(list(self.data["links"][0]))
        self.write("links")
        data = list(run.batches(self.root, self.manifest, "links", 1, 100))
        self.assertEqual([len(part) for part in data], [1, 1])
        self.assertEqual([part[0]["number"] for part in data], [1, 2])

    def test_bad_checkpoint_or_receipt_fails_closed(self):
        owners = FakeOwners()
        call = owners.call
        def wrong(owner, path, body=None):
            result = call(owner, path, body)
            if path.startswith("checkpoint?"):
                result["nextRow"] = 0
            return result
        owners.call = wrong
        with self.assertRaises(run.Blocked):
            run.apply(self.root, self.manifest, owners)


class TransportTests(unittest.TestCase):
    def test_redirect_and_secretless_addresses_rejected(self):
        with self.assertRaises(run.Blocked):
            run.Transport({"company": "http://user:password@company/"}, "synthetic")
        with self.assertRaises(run.Blocked):
            run.Transport({"company": "http://company/"}, "")
        self.assertIsNone(run.NoRedirect().redirect_request(None, None, 302, "", {}, "https://external.invalid"))


if __name__ == "__main__":
    unittest.main()
