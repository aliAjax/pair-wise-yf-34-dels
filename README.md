# 无人机飞行计划审批与空域协调系统

标准库独立项目。系统记录运营方计划、航线、载荷、高度、人口风险和应急方案，检查临时禁飞区、高度范围、人口风险以及相邻有效计划冲突。审核结果支持离线编号幂等回传，计划变更会使原批准失效并生成通知。

空域变更复核：新限制发布后，时空重叠的已批准计划自动转入 `pending_review`（待复核），原批准留档但不可放行，并通知运营方。冲突解除（限制解除或到期）后，审核员按当前版本和处置说明恢复批准，检查不通过则继续保持待复核；运营方改航线、高度或时段会先退回草稿再重新提交。复核规则（`recheck.py` 纯函数）、状态存储（`app.py` 的 `Repository`）与接口入口（`Handler`）分开维护；协调台展示待复核队列，计划状态表样式在 `static/status-table.css` 单独维护。

## 运行

```bash
python3 app.py --db drone_airspace.db
```

默认监听 `127.0.0.1:8205`，首页 `/`，健康检查 `/health`。

身份头为 `X-User-Id`、`X-Role`；运营方还需 `X-Operator`。角色：`viewer`、`operator`、`airspace_reviewer`、`commander`、`auditor`。

## 主要接口

- `POST /api/restrictions`：新增临时限制或禁飞区；重叠的已批准计划转入待复核并通知运营方。
- `POST /api/restrictions/{id}/lift`：解除限制。
- `POST /api/plans`：创建飞行计划。
- `GET /api/plans/{id}/check`：检查硬约束和相邻交通冲突。
- `POST /api/plans/{id}/submit`、`approve`、`reject`：提交和审核；审核使用 `offline_id` 保证断网重连幂等。
- `POST /api/plans/{id}/reinstate`：待复核计划按当前版本和处置说明（`disposition`）恢复批准，检查不通过则保持待复核。
- `POST /api/plans/{id}/change`、`cancel`：版本化变更与取消，并生成通知；待复核计划变更先退回草稿。
- `GET /api/recheck/queue`：待复核队列（审核员、指挥官、审计员）。
- `GET /api/notifications`、`POST /api/expire`：通知与到期处理。
- `GET /api/state`：按角色返回计划、限制和公开信息。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 主要局限

空域几何使用经纬度矩形和航线包围盒近似，不包含多边形、椭球距离、地形、实时遥测和完整间隔标准。紧急授权只能覆盖空域及交通冲突，不能绕过载荷与高度硬限制。身份头、无签名离线审核以及单机 SQLite 适合原型，生产环境需要 PKI、真实 GIS 引擎和跨机构事件总线。
