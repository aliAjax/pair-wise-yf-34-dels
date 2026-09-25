"""空域变更复核规则。

复核规则（本模块）、状态存储（app.Repository）与接口入口（app.Handler）分开维护：
这里只放纯函数和状态常量，不接触数据库与 HTTP，便于单独演进和测试。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

# 待复核：原批准留档但不可放行，也不进入公众有效清单
PENDING_REVIEW = "pending_review"
# 只有已批准计划会被新发布的限制挂起；已提交计划由审核时的冲突检查拦截
SUSPENDABLE_STATUSES = ("approved",)


def _boxes_overlap(a: Sequence[float], b: Sequence[float]) -> bool:
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def _as_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _times_overlap(a_start: datetime | str, a_end: datetime | str, b_start: datetime | str, b_end: datetime | str) -> bool:
    a_start, a_end, b_start, b_end = map(_as_datetime, (a_start, a_end, b_start, b_end))
    return a_start < b_end and b_start < a_end


def restriction_hits_plan(
    restriction: Mapping[str, Any],
    plan_bbox: Sequence[float],
    plan_start: datetime,
    plan_end: datetime,
    plan_max_altitude: float,
) -> bool:
    """新限制与计划时空重叠判定。冲突检查与限制发布挂起共用同一条规则。"""
    rbox = (restriction["min_lon"], restriction["min_lat"], restriction["max_lon"], restriction["max_lat"])
    if not _boxes_overlap(plan_bbox, rbox):
        return False
    if not _times_overlap(plan_start, plan_end, restriction["starts_at"], restriction["ends_at"]):
        return False
    return plan_max_altitude > restriction["min_altitude"] and restriction["max_altitude"] > 0


def should_suspend(status: str) -> bool:
    """限制发布时，哪些状态的计划需要转入待复核。"""
    return status in SUSPENDABLE_STATUSES


def can_reinstate(report: Mapping[str, Any]) -> bool:
    """恢复批准条件：当前版本无硬约束违规且无阻塞冲突；不通过则继续保持待复核。"""
    return not report["hard_violations"] and not report["blocking_conflicts"]
