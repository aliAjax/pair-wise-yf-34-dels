import sys, tempfile, unittest
from datetime import timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import ApiError, DroneAirspaceService, iso, utcnow


class DroneFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.svc = DroneAirspaceService(Path(self.tmp.name) / "test.db"); self.start = utcnow() + timedelta(hours=2)

    def tearDown(self): self.tmp.cleanup()

    def plan(self, callsign="D100", route=None, risk=1, altitude=100):
        return self.svc.create_plan("op-user", "operator", "OP1", {"callsign": callsign, "drone_model": "M400", "payload_kg": 5, "route": route or [[116.1, 39.8], [116.3, 39.9]], "starts_at": iso(self.start), "ends_at": iso(self.start + timedelta(hours=1)), "max_altitude": altitude, "population_risk": risk, "emergency_plan": "返回起降点", "region": "BJ"})

    def test_full_approval_change_and_offline_reconciliation(self):
        plan = self.plan(); submitted = self.svc.submit(plan["id"], "op-user", "operator", "OP1", {})["plan"]
        check = self.svc.check_conflicts(plan["id"], "airspace_reviewer", "")
        self.assertTrue(check["approvable"])
        approved = self.svc.approve(plan["id"], "reviewer", "airspace_reviewer", {"expected_revision": submitted["revision"], "offline_id": "offline-1", "reason": "路线和应急方案满足要求"})
        self.assertEqual(approved["plan"]["status"], "approved")
        duplicate = self.svc.approve(plan["id"], "reviewer", "airspace_reviewer", {"expected_revision": submitted["revision"], "offline_id": "offline-1", "reason": "补传"})
        self.assertTrue(duplicate["idempotent"])
        changed = self.svc.change(plan["id"], "op-user", "operator", "OP1", {"expected_revision": submitted["revision"], "route": [[116.12, 39.82], [116.32, 39.92]]})
        self.assertEqual(changed["status"], "draft"); self.assertEqual(changed["revision"], 2)
        notifications = self.svc.notifications("op-user", "operator", "OP1")["notifications"]
        self.assertEqual(notifications[0]["kind"], "approval_invalidated")

    def overlapping_restriction(self, name="新禁飞区"):
        return self.svc.create_restriction("reviewer", "airspace_reviewer", {"name": name, "kind": "no_fly", "min_lon": 116.0, "min_lat": 39.7, "max_lon": 116.4, "max_lat": 40.0, "min_altitude": 0, "max_altitude": 200, "starts_at": iso(self.start - timedelta(minutes=10)), "ends_at": iso(self.start + timedelta(hours=2)), "reason": "临时活动"})

    def approved_plan(self, callsign, offline_id):
        plan = self.plan(callsign); self.svc.submit(plan["id"], "op-user", "operator", "OP1", {})
        self.svc.approve(plan["id"], "reviewer", "airspace_reviewer", {"expected_revision": 1, "offline_id": offline_id, "reason": "路线和应急方案满足要求"})
        return plan

    def test_recheck_suspend_and_reinstate_after_restriction_lifted(self):
        plan = self.approved_plan("D200", "off-a")
        published = self.overlapping_restriction()
        self.assertIn(plan["id"], published["suspended_plan_ids"])
        suspended = self.svc.get_plan(plan["id"], "airspace_reviewer")
        self.assertEqual(suspended["status"], "pending_review")
        # 原批准留档
        self.assertEqual([a["decision"] for a in suspended["approvals"]], ["approved"])
        # 通知运营方
        kinds = [n["kind"] for n in self.svc.notifications("op-user", "operator", "OP1")["notifications"]]
        self.assertIn("recheck_required", kinds)
        # 不在公众有效清单，但出现在待复核队列
        self.assertEqual(self.svc.state("viewer", "")["plans"], [])
        queue = self.svc.recheck_queue("airspace_reviewer")["queue"]
        self.assertEqual([p["id"] for p in queue], [plan["id"]])
        # 待复核不能直接走批准
        with self.assertRaises(ApiError) as ctx:
            self.svc.approve(plan["id"], "reviewer", "airspace_reviewer", {"expected_revision": 1, "offline_id": "off-b", "reason": "绕过复核"})
        self.assertEqual(ctx.exception.code, "invalid_transition")
        # 限制仍有效，检查不通过，继续保持待复核
        kept = self.svc.reinstate(plan["id"], "reviewer", "airspace_reviewer", {"expected_revision": 1, "disposition": "限制仍有效"})
        self.assertFalse(kept["reinstated"]); self.assertEqual(kept["plan"]["status"], "pending_review")
        # 冲突解除后按当前版本和处置说明恢复批准
        self.svc.lift_restriction(published["id"], "commander", "commander", {"reason": "活动结束"})
        restored = self.svc.reinstate(plan["id"], "reviewer", "airspace_reviewer", {"expected_revision": 1, "disposition": "限制已解除，恢复原批准"})
        self.assertTrue(restored["reinstated"]); self.assertEqual(restored["plan"]["status"], "approved")
        decisions = [a["decision"] for a in self.svc.get_plan(plan["id"], "airspace_reviewer")["approvals"]]
        self.assertEqual(decisions, ["approved", "reinstated"])
        self.assertEqual(self.svc.recheck_queue("airspace_reviewer")["queue"], [])

    def test_recheck_plan_change_returns_to_draft(self):
        plan = self.approved_plan("D201", "off-c")
        self.overlapping_restriction()
        self.assertEqual(self.svc.get_plan(plan["id"], "operator", "OP1")["status"], "pending_review")
        # 运营方改航线、高度或时段：先退回草稿再重提
        changed = self.svc.change(plan["id"], "op-user", "operator", "OP1", {"expected_revision": 1, "max_altitude": 80})
        self.assertEqual(changed["status"], "draft"); self.assertEqual(changed["revision"], 2)
        resubmitted = self.svc.submit(plan["id"], "op-user", "operator", "OP1", {})
        self.assertEqual(resubmitted["plan"]["status"], "submitted")
        # 版本已变，旧版本的恢复申请不能套用
        with self.assertRaises(ApiError) as ctx:
            self.svc.reinstate(plan["id"], "reviewer", "airspace_reviewer", {"expected_revision": 1, "disposition": "旧版本"})
        self.assertEqual(ctx.exception.code, "invalid_transition")

    def test_restriction_emergency_override_and_conflicts(self):
        self.svc.create_restriction("reviewer", "airspace_reviewer", {"name": "临时禁飞", "kind": "no_fly", "min_lon": 116.0, "min_lat": 39.7, "max_lon": 116.2, "max_lat": 40.0, "min_altitude": 0, "max_altitude": 150, "starts_at": iso(self.start - timedelta(minutes=30)), "ends_at": iso(self.start + timedelta(hours=2)), "reason": "活动"})
        plan = self.plan("D101"); self.svc.submit(plan["id"], "op-user", "operator", "OP1", {})
        check = self.svc.check_conflicts(plan["id"], "airspace_reviewer", "")
        self.assertFalse(check["approvable"])
        with self.assertRaises(ApiError) as ctx:
            self.svc.approve(plan["id"], "reviewer", "airspace_reviewer", {"expected_revision": 1, "offline_id": "offline-2", "reason": "常规审核"})
        self.assertEqual(ctx.exception.code, "airspace_conflict")
        override = self.svc.approve(plan["id"], "commander", "commander", {"expected_revision": 1, "offline_id": "offline-3", "reason": "紧急任务", "override_reason": "应急救援授权"})
        self.assertEqual(override["plan"]["status"], "approved")
        conflicting = self.plan("D102", route=[[116.11, 39.81], [116.15, 39.84]])
        self.svc.submit(conflicting["id"], "op-user", "operator", "OP1", {})
        with self.assertRaises(ApiError) as ctx:
            self.svc.approve(conflicting["id"], "reviewer", "airspace_reviewer", {"expected_revision": 1, "offline_id": "offline-4", "reason": "复核"})
        self.assertIn(ctx.exception.code, {"hard_constraint_violation", "airspace_conflict"})
        with self.assertRaises(ApiError) as ctx:
            self.svc.approve(conflicting["id"], "reviewer", "airspace_reviewer", {"expected_revision": 99, "offline_id": "offline-5", "reason": "过期审核"})
        self.assertEqual(ctx.exception.code, "revision_conflict")


if __name__ == "__main__": unittest.main()
