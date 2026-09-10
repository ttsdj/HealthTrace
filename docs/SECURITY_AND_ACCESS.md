# HealthTrace 权限、安全与审计

## 边界模型

HealthTrace 使用四级边界：

```text
User
  -> TenantMembership(owner/admin/clinician/member)
  -> PatientAccessGrant(read/write/manage)
  -> Patient-scoped resources
```

- tenant 成员身份不自动获得所有患者数据。
- 患者访问必须同时满足有效成员关系和有效授权。
- `read` 只能读取，`write` 可以新增和修改患者资料，`manage` 可以管理授权。
- 全局管理员用于平台运维，不替代患者所属机构的授权流程。

### 哪些成员角色不能由 tenant 管理员分配

`POST /tenant/members` 接受 `admin` / `clinician` / `viewer`，但 **`clinician` 只能由全局管理员分配**。

原因：`clinician` 不是租户内部职位，而是平台级临床凭证——
`backend.evaluation.golden_review.reviewer_role()` 接受它作为临床审核意见，
`golden_readiness()["clinical_claim_allowed"]` 正来自该角色，
且该凭证对**所有**数据集生效，与成员所属租户无关。

任何自助注册的用户都会通过 `backend.patient.scope.ensure_user_scope()`
成为其个人租户的 `owner`，因此仅靠租户级校验，任何人都能给自己签发临床审核意见。
分配 `clinician` 必须先通过 `POST /auth/register` 的 `admin_code`
（`ADMIN_INVITE_CODE`）成为全局管理员。

该规则由 `backend/api/routes/access_control.py` 的 `_OPERATOR_ONLY_MEMBER_ROLES`
实现，回归测试见 `tests/test_security_hardening.py`。

## 敏感数据

`patient_sensitive_records` 用于保存不适合明文扩展到业务表的私密 JSON：

- AES-256-GCM 提供机密性和完整性。
- 每条记录使用随机 96-bit nonce。
- tenant、patient、record 和 category 组成 AAD，防止密文跨记录替换。
- 数据库只保存密文和非敏感元数据。
- 密钥来自 `HEALTHTRACE_FIELD_ENCRYPTION_KEY`，不进入 Git。

这不等于整个数据库透明加密。患者事实、时间轴和文档索引仍应运行在启用磁盘加密、备份加密和最小权限账号的数据库中。

## 审计日志

HTTP 中间件记录：

- request ID、路由模板和 HTTP 方法
- 调用用户、tenant/patient scope（能解析时）
- 状态码、耗时和错误类型

审计日志不记录请求正文、回答正文、病历正文、token 或密钥。管理端通过 `/audit/events` 查询。

## 生产要求

1. 用 KMS 或 Secret Manager 托管 JWT、LLM 和字段加密密钥。
2. 数据库账号按环境和服务拆分，禁止使用默认密码。
3. 只允许 HTTPS，限制 CORS 与反向代理来源。
4. 定期验证备份恢复、密钥可用性和审计留存策略。
5. 临床或真实患者数据上线前完成隐私影响评估与访问审批。

