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

### 查询患者记录集合必须带 scope 子句

`healthtrace_patient_record_text_v1` 是全平台共用的集合，**每一个**读它的查询都必须带上
`backend.patient.retrieval.patient_scope_predicate()` 给出的
`document_domain` + `tenant_id` + `patient_id` 子句。该子句只有一份实现，
新调用点应当**导入**它而不是照抄一份。漏斗检索的候选文档查询正是因为漏掉了它，
把其他租户的患者文档 id 随 `rag_trace` 回传给了调用方
（见 `CHANGES_2026-09-10.md` 第 5 节）。

公开医学知识库（`med_medical_qa_v2`）是全局知识，其查询**不应**带租户条件。

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

只对**匹配到路由**的请求写审计。未匹配的 404 没有触达端点、没有读写资源、也没有做授权判断，
因此没有可问责内容；若照写，未认证请求遍历随机 URL 即可持续往审计表插行。
这类请求仍计入指标（统一归入 `route="/unmatched"`，不会为每个 URL 生成一条时间序列）。

已匹配路由上的 401/403/404 是真实授权拒绝，**照常审计**，不因限流而丢弃——
否则攻击者可通过洪泛压制同一出口 IP 下其他用户的审计记录。

## 限流

`/auth/login` 与 `/auth/register` 经 `backend/security/rate_limit.py` 做进程内固定窗口限流，
超限返回 429 + `Retry-After`。默认登录 20 次/60 秒、注册 5 次/300 秒。

两个限制必须在下线前确认：

1. **计数在单 worker 进程内**，N 个 worker 的实际上限是配置值的 N 倍。
2. **客户端身份默认取对端地址**。反向代理后面所有用户共用一个桶，此时需要
   `HEALTHTRACE_TRUST_PROXY_HEADERS=true`；只有在代理由你控制并会覆写
   `X-Forwarded-For` 时才能打开，否则任何客户端都能伪造该头换桶绕过。

因此进程内限流不能替代**反向代理层的限流**，尤其是针对已匹配路由的未认证洪泛
（见 `CHANGES_2026-09-10.md` 第 4.2、4.3 节）。

## 生产要求

1. 用 KMS 或 Secret Manager 托管 JWT、LLM 和字段加密密钥。
2. 数据库账号按环境和服务拆分，禁止使用默认密码。
3. 只允许 HTTPS，限制 CORS 与反向代理来源。
4. 定期验证备份恢复、密钥可用性和审计留存策略。
5. 临床或真实患者数据上线前完成隐私影响评估与访问审批。
6. 在反向代理层配置速率限制与请求体大小上限，不要只依赖应用内限流。


## 令牌吊销（2026-09-12）

- 访问令牌包含 `jti` 声明；`POST /auth/logout` 会把当前令牌的 `jti` 写入 Redis
  denylist（键 `jwt_denylist:<jti>`，TTL 为令牌剩余有效期），`get_current_user`
  每次校验时检查该列表，登出或泄露的令牌在自然过期前即失效。
- denylist 检查与缓存一致采取**尽力而为**语义：Redis 不可用时检查跳过（fail-open），
  不会因缓存故障把全体用户锁在门外；令牌本身仍按短有效期 + fail-closed 签名密钥约束。
- 令牌载体仍是 `localStorage`（已知未修复项），因此吊销机制是 XSS 场景下的兜底，
  不是根治；根治需要迁移到 HttpOnly Cookie 会话。
