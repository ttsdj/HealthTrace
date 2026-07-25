# 存量文档增量更新

## 目标

同一逻辑文档上传新内容时，旧版本在新版本完整准备好之前始终可检索。新版失败不会删除旧向量、旧父块或旧原文件。

## 版本协议

1. 上传到 `data/documents/**/.staging/`，计算文件 SHA-256，不覆盖当前文件。
2. 解析并生成三级父子块；失败时立即停止，当前版本不变。
3. 读取当前 leaf chunks，以 Unicode NFC、空白归一化后的文本 SHA-256 作为内容指纹。
4. 指纹相同的 chunk 复用原 BGE-M3 dense vector；只有新增或改变的 chunk 才调用 embedding。
5. 将新 leaf rows 写入 Milvus，并用动态字段 `index_update_id` 标记本次写入。
6. 原文件先保存到 `.versions/`，再原子切换当前文件。
7. 在一个 PostgreSQL 事务中替换父块、切换 `DocumentVersion` 和 `DocumentRecord`。
8. 按 Milvus 主键删除旧 leaf rows；事务成功后，新版本成为 active。

同一进程使用 document-level lock；PostgreSQL 部署额外使用 session advisory lock，防止多 worker 同时更新同一逻辑文档。

## 失败补偿

| 失败位置 | 补偿动作 |
| --- | --- |
| 解析或 embedding | 不写新版本，旧版本不变 |
| 新向量写入 | 按新 Milvus 主键删除本次 rows |
| 原文件切换 | 从 `.versions/` 恢复旧文件 |
| PostgreSQL 或旧向量删除 | 回滚 SQL 事务、删除新 rows、恢复旧文件 |
| 旧向量已删后 SQL 提交失败 | 使用预读的旧 dense vectors 重建旧 rows |

`document_versions` 保存 active、superseded 和 failed 版本审计。回滚 Phase 7 只关闭功能，不删除版本记录。

## API

公共知识库：

```text
POST /documents/upload
POST /documents/upload/async
GET  /documents/{filename}/versions
```

同名公共文件自动视为同一逻辑文档的新版本。

患者私有文档：

```text
PUT /patient/documents/{document_id}
PUT /patient/documents/{document_id}/async
GET /patient/documents/{document_id}/versions
```

患者接口继续执行 tenant、patient 和 owner scope 校验。

## 运维

```cmd
.venv\Scripts\python.exe scripts\migrate_phase7_incremental_documents.py status
.venv\Scripts\python.exe scripts\migrate_phase7_incremental_documents.py apply
.venv\Scripts\python.exe scripts\migrate_phase7_incremental_documents.py rollback
```

紧急关闭：

```env
HEALTHTRACE_INCREMENTAL_UPDATE_ENABLED=false
```

当前协议是跨 PostgreSQL、Milvus 和文件系统的 Saga，不是假装存在分布式 ACID。切换的短窗口内，新旧 leaf row 可能同时可见；失败补偿和检索去重用于保证最终一致性。历史原文件需要后续配置保留期限和配额，不能无限增长。
