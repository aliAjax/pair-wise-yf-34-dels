"""空域变更复核规则。

纯函数模块：只依赖传入的普通数据结构，不接触数据库与 HTTP，
与状态存储（recheck_store.py）、接口入口（app.py）分开维护。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

# 计划侧状态：限制发布后，时空重叠的已批准计划转入待复核，原批准留档但暂停放行
PLAN_UNDER_REVIEW = "under_review"

# 复核单状态
RECHECK_PENDING = "pending"        # 待复核
RECHECK_REINSTATED = "reinstated"  # 已按当前版本恢复批准
RECHECK_SUPERSEDED = "superseded"  # 计划已变更/过期，复核单关闭

# 复核结论
VERDICT_CLEAR = "clear"                      # 冲突已解除，可恢复批准
VERDICT_CONFLICT_REMAINS = "conflict_remains"  # 仍有空域或交通冲突，保持待复核
VERDICT_HARD_VIOLATION = "hard_violation"    # 触碰硬约束，保持待复核


def boxes_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float], buffer: float = 0.0) -> bool:
    return a[0] <= b[2] + buffer and a[2] + buffer >= b[0] and a[1] <= b[3] + buffer and a[3] + buffer >= b[1]


def times_overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def plan_hits_restriction(plan_bbox: tuple[float, float, float, float], plan_start: datetime, plan_end: datetime,
                          plan_max_altitude: float, restriction: dict[str, Any]) -> bool:
    """判断计划与新发布限制是否时空重叠（几何规则与放行检查一致）。

    restriction 的 starts_at/ends_at 必须是 datetime，其余字段为限制表的数值字段。
    """
    rbox = (restriction["min_lon"], restriction["min_lat"], restriction["max_lon"], restriction["max_lat"])
    if not boxes_overlap(plan_bbox, rbox):
        return False
    if not times_overlap(plan_start, plan_end, restriction["starts_at"], restriction["ends_at"]):
        return False
    return plan_max_altitude > restriction["min_altitude"] and restriction["max_altitude"] > 0


def reinstate_verdict(report: dict[str, Any]) -> tuple[bool, str]:
    """根据当前版本的冲突检查结果给出复核结论。检查不通过就继续保持待复核。"""
    if report["hard_violations"]:
        return False, VERDICT_HARD_VIOLATION
    if report["blocking_conflicts"]:
        return False, VERDICT_CONFLICT_REMAINS
    return True, VERDICT_CLEAR
