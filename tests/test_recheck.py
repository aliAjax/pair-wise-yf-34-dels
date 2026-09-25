import sys, tempfile, unittest
from datetime import timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import ApiError, DroneAirspaceService, iso, utcnow


class RecheckFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.svc = DroneAirspaceService(Path(self.tmp.name) / "test.db"); self.start = utcnow() + timedelta(hours=2)

    def tearDown(self): self.tmp.cleanup()

    def approved_plan(self, callsign="D300"):
        plan = self.svc.create_plan("op-user", "operator", "OP1", {"callsign": callsign, "drone_model": "M400", "payload_kg": 5, "route": [[116.1, 39.8], [116.3, 39.9]], "starts_at": iso(self.start), "ends_at": iso(self.start + timedelta(hours=1)), "max_altitude": 100, "population_risk": 1, "emergency_plan": "返回起降点", "region": "BJ"})
        self.svc.submit(plan["id"], "op-user", "operator", "OP1", {})
        self.svc.approve(plan["id"], "reviewer", "airspace_reviewer", {"expected_revision": 1, "offline_id": f"off-{callsign}", "reason": "常规批准"})
        return plan

    def publish_restriction(self):
        return self.svc.create_restriction("reviewer", "airspace_reviewer", {"name": "新禁飞区", "kind": "no_fly", "min_lon": 116.0, "min_lat": 39.7, "max_lon": 116.4, "max_lat": 40.0, "min_altitude": 0, "max_altitude": 150, "starts_at": iso(self.start - timedelta(minutes=30)), "ends_at": iso(self.start + timedelta(hours=2)), "reason": "临时活动"})

    def test_restriction_flags_approved_plan_and_reinstate_after_lift(self):
        plan = self.approved_plan()
        published = self.publish_restriction()
        self.assertEqual([f["plan_id"] for f in published["flagged_plans"]], [plan["id"]])
        view = self.svc.get_plan(plan["id"], "airspace_reviewer")
        self.assertEqual(view["status"], "under_review")
        self.assertEqual(view["approvals"][0]["decision"], "approved")  # 原批准留档
        notes = self.svc.notifications("op-user", "operator", "OP1")["notifications"]
        self.assertEqual(notes[0]["kind"], "recheck_required")  # 通知运营方
        public = self.svc.state("viewer", "")
        self.assertNotIn(plan["id"], [p["id"] for p in public["plans"]])  # 不能放行：公众视图不再有效
        queue = self.svc.recheck_queue("airspace_reviewer", "")["rechecks"]
        self.assertEqual([(q["plan_id"], q["plan_status"]) for q in queue], [(plan["id"], "under_review")])
        self.assertEqual(self.svc.recheck_queue("operator", "OP1")["rechecks"][0]["callsign"], plan["callsign"])
        with self.assertRaises(ApiError) as ctx:  # 待复核计划不能直接提交
            self.svc.submit(plan["id"], "op-user", "operator", "OP1", {})
        self.assertEqual(ctx.exception.code, "invalid_transition")
        kept = self.svc.reinstate(plan["id"], "reviewer", "airspace_reviewer", {"expected_revision": 1, "disposition": "限制仍在生效"})
        self.assertFalse(kept["reinstated"]); self.assertEqual(kept["verdict"], "conflict_remains")
        self.assertEqual(kept["plan"]["status"], "under_review")  # 检查没通过就继续保持
        self.svc.lift_restriction(published["id"], "commander", "commander", {"reason": "活动结束"})
        done = self.svc.reinstate(plan["id"], "reviewer", "airspace_reviewer", {"expected_revision": 1, "disposition": "限制已解除，恢复原批准"})
        self.assertTrue(done["reinstated"]); self.assertEqual(done["plan"]["status"], "approved")
        decisions = [a["decision"] for a in self.svc.get_plan(plan["id"], "airspace_reviewer")["approvals"]]
        self.assertEqual(decisions, ["approved", "reinstated"])
        self.assertEqual(self.svc.recheck_queue("airspace_reviewer", "")["rechecks"], [])
        again = self.svc.reinstate(plan["id"], "reviewer", "airspace_reviewer", {"expected_revision": 1, "disposition": "断网重传"})
        self.assertTrue(again["idempotent"])

    def test_operator_change_returns_to_draft_and_supersedes_recheck(self):
        plan = self.approved_plan("D301")
        self.publish_restriction()
        changed = self.svc.change(plan["id"], "op-user", "operator", "OP1", {"expected_revision": 1, "route": [[117.1, 40.5], [117.3, 40.6]]})
        self.assertEqual(changed["status"], "draft"); self.assertEqual(changed["revision"], 2)  # 先退回草稿
        self.assertEqual(self.svc.recheck_queue("airspace_reviewer", "")["rechecks"], [])  # 旧复核单关闭
        with self.assertRaises(ApiError) as ctx:  # 已变更计划不能走复核恢复
            self.svc.reinstate(plan["id"], "reviewer", "airspace_reviewer", {"expected_revision": 2, "disposition": "已改航线"})
        self.assertEqual(ctx.exception.code, "invalid_transition")
        self.svc.submit(plan["id"], "op-user", "operator", "OP1", {})  # 再重提
        approved = self.svc.approve(plan["id"], "reviewer", "airspace_reviewer", {"expected_revision": 2, "offline_id": "off-d301-r2", "reason": "新航线不在限制区"})
        self.assertEqual(approved["plan"]["status"], "approved")

    def test_reinstate_requires_matching_revision_and_reviewer_role(self):
        plan = self.approved_plan("D302")
        self.publish_restriction()
        with self.assertRaises(ApiError) as ctx:
            self.svc.reinstate(plan["id"], "reviewer", "airspace_reviewer", {"expected_revision": 9, "disposition": "版本不符"})
        self.assertEqual(ctx.exception.code, "revision_conflict")
        with self.assertRaises(ApiError) as ctx:
            self.svc.reinstate(plan["id"], "op-user", "operator", {"expected_revision": 1, "disposition": "越权"})
        self.assertEqual(ctx.exception.code, "review_forbidden")


if __name__ == "__main__": unittest.main()
