import {BusinessError} from './errors';
import {withExportGuard} from './export-security';
import {McpExportFiles} from './mcp-export-files';
import { McpPayrollExportService,mcpPayrollExportTool } from './mcp-payroll-export';import { McpReconExportService,mcpReconExportTool } from './mcp-recon-export';
import { McpRebuildService, mcpRebuildTool } from './mcp-rebuild';
import { McpUploadService, mcpUploadTool } from './mcp-upload';
import { McpTaskImportService, mcpTaskImportTool } from './mcp-task-import';
import { McpStoreWriteService, isMcpStoreAction, isMcpStoreWrite, mcpStoreDefinition } from './mcp-store-write';
import { McpTaskWriteService, isMcpTaskWrite, mcpTaskWriteTools } from './mcp-task-write';
import { McpManagementWriteService, isMcpManagementWrite, mcpManagementWriteDefinitions } from './mcp-management-write';
import { McpIntegrityReadService, mcpIntegrityReadTools } from './mcp-integrity-read';
import { McpManagementReadService, mcpManagementReadTools } from './mcp-management-read';
import { McpFinanceReadService, mcpFinanceReadTools } from './mcp-finance-read';
import { McpTaskReadService, mcpTaskReadTools } from './mcp-task-read';
import { McpReadService, McpReadError, mcpReadTools } from './mcp-read';
import { Inject, Injectable } from '@nestjs/common';
import { createHash, timingSafeEqual } from 'node:crypto';
import type { Request, Response, NextFunction } from 'express';
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { CONFIG, AppConfig } from './config';
import { Database } from './database';

export interface McpActor {
  uid: number; tokenId: number; role: 'admin' | 'leader' | 'staff'; scopes: string[];
  personCode: string | null; username: string; displayName: string; personName: string | null;
}
interface TokenRow {
  uid: number; token_id: number; token_hash: string; scopes: string; role: string;
  revoked_at: Date | null; expires_at: Date | null; status: string; is_active: boolean;
  must_change_password: boolean; person_code: string | null; username: string;
  display_name: string; person_name: string | null;
}
export const MCP_STAFF_TOOLS = new Set(['visit_whoami', 'visit_my_perf', 'visit_my_pay', 'visit_my_tasks', 'visit_self_report', 'visit_task_report']);
export const MCP_LEADER_TOOLS = new Set([...MCP_STAFF_TOOLS, 'visit_team_tasks', 'visit_task_assign', 'visit_task_confirm', 'visit_task_transfer']);
export function mcpRoleAllows(role: McpActor['role'], tool: string) {
  return role === 'admin' || (role === 'leader' ? MCP_LEADER_TOOLS : MCP_STAFF_TOOLS).has(tool);
}
export function mcpError(code: string, message: string, hint = '', retryable = false, details: Record<string, unknown> = {}) {
  return { ok: false as const, error: { code, message, retryable, hint, ...details } };
}
function whoami(actor: McpActor) {
  const admin = actor.role === 'admin', leader = actor.role === 'leader', write = actor.scopes.includes('write');
  const capabilities = admin ? [
    '查看公司级绩效、薪资、找平、对账及文件/账号/配置/门店数据',
    ...(write ? ['上传巡店/对账文件及任务计划，维护员工状态与业务配置、门店主档',
      '按当前业务保护执行作业管理、月重算及发薪导出登记'] : []),
    '导出已有对账结果；预演、确认语和版本参数按各工具要求提供',
  ] : ['查看本人绩效、逐日明细、找平发放及本人任务',
    ...(leader ? ['查询当前有效本队任务，不读取其他队数据'] : []),
    ...(write ? ['提交本人自报与有权操作的任务进度', ...(leader ? ['按当前本队权限派工、转队、确认或驳回进展'] : [])] : []),
    '不能查询他人工资或公司级数据；队长身份仍需有效团队关系',
  ];
  return {
    username: actor.username, display_name: actor.displayName, role: actor.role,
    role_label: admin ? '管理员' : leader ? '队长' : '员工', person_code: actor.personCode, person_name: actor.personName,
    scopes: actor.scopes, can_write: write, capabilities, currency: 'JPY',
    hint: (admin ? '管理员身份：按已公布工具操作。' : leader ? '队长身份：本人数据与当前有效本队作业。' : '员工身份：仅本人数据及本人有权执行的作业。') +
      (write ? '写入仍受角色、成员关系、业务状态及确认/版本规则限制。' : '当前凭据只读；发现写工具不表示可以执行写入。'),
  };
}
const identityTool = {
  name: 'visit_whoami', title: '我是谁', description: '查看当前账号、角色、绑定人员和权限范围。只读；无需参数。',
  inputSchema: { type: 'object' as const, properties: {}, additionalProperties: false },
  annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
};

@Injectable()
export class McpService {
  constructor(@Inject(Database) private readonly db: Database, @Inject(CONFIG) private readonly config: AppConfig, @Inject(McpReadService) private readonly reads: McpReadService, @Inject(McpTaskReadService) private readonly taskReads: McpTaskReadService, @Inject(McpFinanceReadService) private readonly financeReads: McpFinanceReadService, @Inject(McpManagementReadService) private readonly managementReads: McpManagementReadService, @Inject(McpIntegrityReadService) private readonly integrityReads: McpIntegrityReadService, @Inject(McpManagementWriteService) private readonly managementWrites: McpManagementWriteService, @Inject(McpTaskWriteService) private readonly taskWrites: McpTaskWriteService, @Inject(McpStoreWriteService) private readonly storeWrites: McpStoreWriteService, @Inject(McpTaskImportService) private readonly taskImport: McpTaskImportService, @Inject(McpUploadService) private readonly uploads: McpUploadService, @Inject(McpRebuildService) private readonly rebuilds: McpRebuildService, @Inject(McpPayrollExportService) private readonly payrollExports: McpPayrollExportService, @Inject(McpReconExportService) private readonly reconExports: McpReconExportService, @Inject(McpExportFiles) private readonly exportFiles: McpExportFiles) {}

  private async resolve(raw: string, client: Pick<Database, 'query'>, lock = false): Promise<McpActor | null> {
    if (!/^[A-Za-z0-9_-]{8,512}$/.test(raw)) return null;
    const row = (await client.query<TokenRow>(`SELECT t.id AS token_id,t.token_hash,t.scopes,t.revoked_at,t.expires_at,
      u.id AS uid,u.username,u.display_name,u.person_code,u.role,u.status,u.is_active,u.must_change_password,
      p.display_name AS person_name FROM api_tokens t JOIN users u ON u.id=t.user_id
      LEFT JOIN persons p ON p.code=u.person_code WHERE t.token_prefix=$1 ${lock ? 'FOR SHARE OF t,u' : ''}`, [raw.slice(0, 8)])).rows[0];
    if (!row || !/^[a-f0-9]{64}$/.test(row.token_hash)) return null;
    const actual = createHash('sha256').update(raw).digest();
    if (!timingSafeEqual(actual, Buffer.from(row.token_hash, 'hex'))) return null;
    if (row.revoked_at || row.expires_at && row.expires_at.getTime() <= Date.now() ||
      !row.is_active || row.status !== 'active' || row.must_change_password || !['admin', 'leader', 'staff'].includes(row.role)) return null;
    const scopes = (row.scopes || 'read').split(',').map(scope => scope.trim()).filter(Boolean);
    if (!scopes.includes('read') || scopes.some(scope => !['read', 'write'].includes(scope))) return null;
    return { uid: row.uid, tokenId: row.token_id, role: row.role as McpActor['role'], scopes,
      personCode: row.person_code, username: row.username, displayName: row.display_name || '', personName: row.person_name };
  }

  private resourceUrl() {
    return this.config.mcpPublicUrl || `http://${this.config.host}:${this.config.port}/mcp`;
  }

  private async read(raw: string, initial: McpActor, tool: string, args: Record<string, unknown>, clientInfo: string | null) {
    if(initial.role==='admin'&&['visit_recon_export','visit_payroll_export'].includes(tool)){
      try{return await withExportGuard(this.db,initial.uid,tool,String(args.task_id??args.month??''),()=>this.readTool(raw,initial,tool,args,clientInfo),result=>result.ok);}
      catch(error){return error instanceof BusinessError?mcpError(error.code,error.message):mcpError('INTERNAL','导出暂时不可用');}
    }
    return this.readTool(raw,initial,tool,args,clientInfo);
  }
  private async readTool(raw: string, initial: McpActor, tool: string, args: Record<string, unknown>, clientInfo: string | null) {
    // Persist only an allowlisted parameter shape. Arbitrary arguments may contain credentials or private data.
    const audit = (await this.db.query<{ id: number }>(`INSERT INTO mcp_audit_log
      (token_id,user_id,tool,params_json,ok,client_info,created_at) VALUES($1,$2,$3,'{}',NULL,$4,now()) RETURNING id`,
    [initial.tokenId, initial.uid, tool, clientInfo])).rows[0];
    const started = performance.now();
    const write = tool === 'visit_recon_export' || tool === 'visit_payroll_export' || tool === 'visit_rebuild' || tool === 'visit_upload' || tool === 'visit_task_import' || isMcpManagementWrite(tool, args) || isMcpTaskWrite(tool, args) || isMcpStoreWrite(tool, args);
    let auditFinalized = false;
    const exportCleanup: (() => Promise<void>)[] = [];
    const onExport = (cleanup: () => Promise<void>) => { exportCleanup.push(cleanup); };
    let afterCommit: (() => Promise<Record<string, unknown>>) | undefined;
    let result: ReturnType<typeof mcpError> | { ok: true; data: unknown };
    try {
      result = await this.db.transaction(async client => {
        if (!write) await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');
        else if (tool === 'visit_recon_export') await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');
        const actor = await this.resolve(raw, client, write);
        // A changed binding/role invalidates this in-flight actor. A fresh request may use its new permissions.
        if (!actor || actor.uid !== initial.uid || actor.role !== initial.role || actor.personCode !== initial.personCode ||
          actor.scopes.join(',') !== initial.scopes.join(',')) return mcpError('UNAUTHORIZED', '凭据或账号状态已变化', '请重新连接');
        if (write) {
          const upload = tool === 'visit_upload' ? await this.uploads.prepare(client, actor, args) : undefined;
          afterCommit = upload?.afterCommit;
          const data = tool === 'visit_rebuild' ? await this.rebuilds.run(client, actor, args, audit.id) : tool === 'visit_payroll_export' ? await this.payrollExports.run(client, actor, args, audit.id, onExport) : tool === 'visit_recon_export' ? await this.reconExports.run(client, actor, args, audit.id, onExport) : upload ? upload.data : tool === 'visit_task_import' ? await this.taskImport.run(client, actor, args) : isMcpStoreWrite(tool, args) ? await this.storeWrites.run(client, actor, args, audit.id) : await (isMcpTaskWrite(tool, args) ? this.taskWrites : this.managementWrites).run(client, actor, tool, args);
          // Success audit commits atomically with the business write. Audit failure rolls back the write.
          await client.query('UPDATE mcp_audit_log SET ok=true,error_code=NULL,duration_ms=$2 WHERE id=$1', [audit.id, Math.round(performance.now() - started)]);
          return { ok: true as const, data };
        }
        if (isMcpStoreAction(tool, args)) return { ok: true as const, data: await this.storeWrites.run(client, actor, args, audit.id) };
        if (tool !== 'visit_whoami') {
          if (!mcpRoleAllows(actor.role, tool)) return mcpError('FORBIDDEN_TOOL', '没有该工具的权限');
          return { ok: true as const, data: await (tool === 'visit_day_report' ? this.taskWrites : mcpTaskReadTools.some(item => item.name === tool) ? this.taskReads : mcpFinanceReadTools.some(item => item.name === tool) ? this.financeReads : mcpManagementReadTools.some(item => item.name === tool) ? this.managementReads : mcpIntegrityReadTools.some(item => item.name === tool) ? this.integrityReads : this.reads).run(client, actor, tool, args) };
        }
        const identityKeys = ['person', 'person_code', 'person_id'];
        if (identityKeys.some(key => key in args && args[key] !== actor.personCode)) {
          return mcpError('FORBIDDEN_TOOL', '不能查询其他人员的身份', '本工具仅返回当前凭据绑定身份');
        }
        if (Object.keys(args).length) return mcpError('BAD_PARAM', '本工具无需参数');
        return { ok: true as const, data: whoami(actor) };
      });
      auditFinalized = write && result.ok;
      if (result.ok && afterCommit) {
        const accepted = result.data as Record<string, unknown>;
        try { result = { ok: true, data: await afterCommit() }; }
        catch { result = { ok: true, data: { ...accepted, note: '上传已登记，但暂无法确认后台状态；请按任务编号查询，勿盲目重传' } }; }
      }
    } catch (error) {
      result = error instanceof McpReadError ? mcpError(error.code, error.message, error.hint, false, error.details) : mcpError('INTERNAL', '查询暂时不可用', '稍后重试', true);
    }
    if (!result.ok) await Promise.allSettled(exportCleanup.map(cleanup => cleanup()));
    if (!auditFinalized) await this.db.query(`UPDATE mcp_audit_log SET ok=$2,error_code=$3,duration_ms=$4 WHERE id=$1`,
      [audit.id, result.ok, result.ok ? null : result.error.code, Math.round(performance.now() - started)]);
    // Usage bookkeeping cannot turn a successful read into a failed business operation.
    try { await this.db.query('UPDATE api_tokens SET last_used_at=now() WHERE id=$1 AND revoked_at IS NULL', [initial.tokenId]); } catch { /* best effort */ }
    return result;
  }

  async handle(req: Request, res: Response, next: NextFunction) {
    if (req.path === '/.well-known/oauth-protected-resource') {
      res.json({ resource: this.resourceUrl(), authorization_servers: [new URL(this.resourceUrl()).origin], bearer_methods_supported: ['header'], scopes_supported: ['read', 'write'] });
      return;
    }
    const exportRequest = /^\/mcp\/exports\/([a-f0-9-]{36})$/.exec(req.path);
    if (!exportRequest && req.path !== '/mcp' && req.path !== '/mcp/') { next(); return; }
    // Stateless requests never share identity or transport state, even when a client reuses an HTTP connection.
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    if (req.method !== (exportRequest ? 'GET' : 'POST')) { res.setHeader('Allow', exportRequest ? 'GET' : 'POST'); res.status(405).json(mcpError('METHOD_NOT_ALLOWED', '仅支持 POST')); return; }
    const raw = /^Bearer ([A-Za-z0-9_-]{8,512})$/i.exec(req.headers.authorization || '')?.[1];
    let actor: McpActor | null;
    try { actor = raw ? await this.resolve(raw, this.db) : null; } catch {
      res.status(503).json(mcpError('INTERNAL', '身份服务暂时不可用', '稍后重试', true)); return;
    }
    if (!actor || !raw) {
      const metadata = new URL('/.well-known/oauth-protected-resource', this.resourceUrl()).href;
      res.setHeader('WWW-Authenticate', `Bearer resource_metadata="${metadata}"`);
      res.status(401).json(mcpError('UNAUTHORIZED', '凭据无效或账号不可用', '请重新连接或联系管理员')); return;
    }
    if (exportRequest) {
      try {
        const file = await this.exportFiles.download(exportRequest[1], actor);
        res.setHeader('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
        res.setHeader('Content-Disposition',`attachment; filename="export.xlsx"; filename*=UTF-8''${encodeURIComponent(file.filename)}`);
        res.send(file.bytes);
      } catch (error) {
        const known = error instanceof McpReadError;
        res.status(known ? error.code === 'FORBIDDEN_TOOL' ? 403 : 404 : 500).json(known ? mcpError(error.code,error.message,error.hint) : mcpError('INTERNAL','导出暂不可下载'));
      }
      return;
    }
    let body = req.body;
    if (body === undefined) {
      const chunks: Buffer[] = []; let size = 0;
      try {
        for await (const chunk of req) { const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk); size += bytes.length;
          if (size > 24 * 1024 * 1024) { res.status(413).json(mcpError('BAD_REQUEST', 'MCP请求超过24MiB')); return; } chunks.push(bytes); }
        body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      } catch { res.status(400).json(mcpError('BAD_REQUEST', 'MCP请求JSON无效')); return; }
    }
    const server = new Server({ name: 'visit-settle-mcp', version: '0.1.0' }, { capabilities: { tools: {} } });
    server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: [identityTool, ...mcpReadTools, ...mcpTaskReadTools, ...mcpFinanceReadTools, ...mcpManagementReadTools, ...mcpIntegrityReadTools, ...mcpTaskWriteTools, mcpTaskImportTool, mcpUploadTool, mcpRebuildTool, mcpPayrollExportTool, mcpReconExportTool].filter(tool => mcpRoleAllows(actor.role, tool.name)).map(tool => { const mixed = tool.name === 'visit_store' ? mcpStoreDefinition : mcpManagementWriteDefinitions[tool.name as keyof typeof mcpManagementWriteDefinitions]; return mixed ? { ...tool, ...mixed } : tool; }) }));
    server.setRequestHandler(CallToolRequestSchema, async request => {
      let value;
      if (!mcpRoleAllows(actor.role, request.params.name)) value = mcpError('FORBIDDEN_TOOL', '没有该工具的权限');
      else if (![identityTool, ...mcpReadTools, ...mcpTaskReadTools, ...mcpFinanceReadTools, ...mcpManagementReadTools, ...mcpIntegrityReadTools, ...mcpTaskWriteTools, mcpTaskImportTool, mcpUploadTool, mcpRebuildTool, mcpPayrollExportTool, mcpReconExportTool].some(tool => tool.name === request.params.name)) value = mcpError('NOT_FOUND', '工具尚未恢复', '请使用已公布的工具');
      else {
        try {
          // Client metadata is deliberately omitted until a shared redaction policy exists.
          value = await this.read(raw, actor, request.params.name, request.params.arguments ?? {}, null);
        } catch { value = mcpError('INTERNAL', '审计服务暂时不可用', '稍后重试', true); }
      }
      return { content: [{ type: 'text' as const, text: JSON.stringify(value) }], isError: !value.ok };
    });
    const url = new URL(this.resourceUrl());
    const localPort = req.socket.localPort;
    const localHosts = [`${this.config.host}:${localPort}`, `localhost:${localPort}`];
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined, enableJsonResponse: true, enableDnsRebindingProtection: true,
      allowedHosts: [url.host, ...localHosts], allowedOrigins: [url.origin, ...localHosts.map(host => `http://${host}`)],
    });
    let closed = false;
    const close = () => { if (!closed) { closed = true; void server.close().catch(() => {}); } };
    res.once('close', close);
    try { await server.connect(transport); await transport.handleRequest(req, res, body); }
    catch { if (!res.headersSent) res.status(500).json(mcpError('INTERNAL', 'MCP 请求处理失败', '稍后重试', true)); else if (!res.writableEnded) res.end(); }
    finally { if (res.writableEnded) close(); }
  }
}
