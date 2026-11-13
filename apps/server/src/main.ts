import {McpCredentialsController,McpCredentialsService} from './mcp-credentials';
import {McpOAuthService} from './mcp-oauth';
import {McpOAuthController,McpOAuthHttp} from './mcp-oauth-http';
import {McpExportFiles} from './mcp-export-files';
import { McpPayrollExportService } from './mcp-payroll-export';
import { McpReconExportService } from './mcp-recon-export';
import { McpRebuildService } from './mcp-rebuild';
import { McpUploadService } from './mcp-upload';
import { McpTaskImportService } from './mcp-task-import';
import { McpStoreWriteService } from './mcp-store-write';
import { McpTaskWriteService } from './mcp-task-write';
import { McpManagementWriteService } from './mcp-management-write';
import { McpIntegrityReadService } from './mcp-integrity-read';
import { McpManagementReadService } from './mcp-management-read';
import { McpFinanceReadService } from './mcp-finance-read';
import { McpTaskReadService } from './mcp-task-read';
import { McpReadService } from './mcp-read';
import { McpService } from './mcp';
import {OperationsLogsController,OperationsLogsService} from './operations-logs';
import {MerchantMapController,MerchantMapService} from './merchant-map';
import {LegacyWorkController} from './legacy-work';
import {ZoneAssetsController,ZoneAssetsService} from './zone-assets';
import {BatchWorkController,BatchWorkService,BatchPhotoUploadGuard} from './batch-work';
import {StationSequenceController,StationSequenceService} from './station-sequence';
import {RailImportController,RailImportService} from './rail-import';
import {StationRebuildController,StationRebuildService} from './station-rebuild';
import {StationAssetsController,StationAssetsService} from './station-assets';
import {StationExportController,StationExportService} from './station-export';
import {SelfReportController,SelfReportService} from './self-report';
import {StationTasksController,StationTasksService} from './station-tasks';
import {TeamsController,TeamsService} from './teams';
import {ImportReportController,ImportReportService} from './import-report';
import 'reflect-metadata';
import { Module } from '@nestjs/common';
import { NestFactory } from '@nestjs/core';
import { randomUUID } from 'node:crypto';
import type { Request, Response, NextFunction } from 'express';
import { CONFIG, AppConfig, loadConfig } from './config';
import { Database } from './database';
import { AuthService } from './auth';
import { AuthController, ReadController } from './controllers';
import { EmployeesController } from './employees';
import { EmployeeService } from './employee-service';
import { DailyReportsController, DailyReportsService } from './daily-reports';
import { ReportComparisonService } from './report-comparison';
import { PlansController, PlansService } from './plans';
import { MessagesController, MessagesService } from './messages';
import { ReportAnalysisController, ReportAnalysisService } from './report-analysis';
import { ReportExportController } from './report-export';
import { DashboardController, DashboardService } from './dashboard';
import { PayrollController, PayrollService } from './payroll';
import { PayrollPaymentsController, PayrollPaymentsService } from './payroll-payments';
import { SettlementController, SettlementService } from './settlement';
import { SettlementSyncService } from './settlement-sync';
import { SettlementJobsController, SettlementJobsService } from './settlement-jobs';
import { AdminUploadGuard, VisitImportsController, VisitImportsService } from './visit-imports';
import { ReconciliationController, ReconciliationService } from './reconciliation';
import { PayrollExportController, PayrollExportService } from './payroll-export';
import { ReconciliationExportController, ReconciliationExportService } from './reconciliation-export';
import { ConfigurationController, ConfigurationService } from './configuration';
import { ImportDeletionController, ImportDeletionService } from './import-deletion';
import { StoreMasterController, StoreMasterService } from './store-master';
import { PerformanceDailyController, PerformanceDailyService } from './performance-daily';
import { ReconciliationInsightController, ReconciliationInsightService } from './reconciliation-insight';
import { StoreAiController, StoreAiService } from './store-ai';
import { ReconciliationConfirmationController, ReconciliationConfirmationService } from './reconciliation-confirmation';
import { ApiExceptionFilter } from './errors';

export async function createApp(config: AppConfig) {
  @Module({ controllers: [McpCredentialsController,McpOAuthController,OperationsLogsController,MerchantMapController,LegacyWorkController,ZoneAssetsController,AuthController, ReadController, EmployeesController, DailyReportsController, PlansController, MessagesController, ReportAnalysisController, ReportExportController, DashboardController, PayrollController, PayrollPaymentsController, SettlementController, SettlementJobsController, VisitImportsController, ReconciliationController, PayrollExportController, ReconciliationExportController, ConfigurationController, ReconciliationConfirmationController, ImportDeletionController, StoreMasterController, PerformanceDailyController, ReconciliationInsightController, StoreAiController,ImportReportController,TeamsController,StationTasksController,SelfReportController,StationExportController,StationAssetsController,StationRebuildController,RailImportController,StationSequenceController,BatchWorkController], providers: [McpCredentialsService,McpOAuthService,McpOAuthHttp,ReconciliationExportService,McpExportFiles,McpPayrollExportService,McpReconExportService,McpRebuildService,McpUploadService,McpTaskImportService,McpStoreWriteService,McpTaskWriteService,McpManagementWriteService,McpIntegrityReadService,McpManagementReadService,McpFinanceReadService,McpTaskReadService,McpReadService,McpService,OperationsLogsService,MerchantMapService,ZoneAssetsService,Database, AuthService, EmployeeService, DailyReportsService, ReportComparisonService, PlansService, MessagesService, ReportAnalysisService, DashboardService, PayrollService, PayrollPaymentsService, SettlementService, SettlementSyncService, SettlementJobsService,VisitImportsService,AdminUploadGuard,ReconciliationService,PayrollExportService,ConfigurationService,ReconciliationConfirmationService,ImportDeletionService,StoreMasterService,PerformanceDailyService,ReconciliationInsightService,StoreAiService,ImportReportService,TeamsService,StationTasksService,SelfReportService,StationExportService,StationAssetsService,StationRebuildService,RailImportService,StationSequenceService,BatchWorkService,BatchPhotoUploadGuard,{provide:'SETTLEMENT_JOBS',useExisting:SettlementJobsService}, { provide: CONFIG, useValue: config }] })
  class AppModule {}
  const app = await NestFactory.create(AppModule, { logger: ['error', 'warn'], bodyParser: true });
  app.setGlobalPrefix('api');
  app.useGlobalFilters(new ApiExceptionFilter());
  app.use((req: Request, res: Response, next: NextFunction) => {
    const started = performance.now();
    const requestId = randomUUID();
    res.setHeader('X-Request-ID', requestId);
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('X-Frame-Options','DENY');
    res.setHeader('Referrer-Policy','strict-origin-when-cross-origin');
    res.setHeader('Content-Security-Policy',"frame-ancestors 'none'; object-src 'none'; base-uri 'self'");
    if(config.cookieSecure)res.setHeader('Strict-Transport-Security','max-age=31536000');
    res.on('finish', () => console.log(JSON.stringify({ event: 'request_end', requestId, method: req.method, path: req.path,
      status: res.statusCode, durationMs: Math.round(performance.now() - started), rss: process.memoryUsage().rss })));
    next();
  });
  const oauth=app.get(McpOAuthHttp);
  app.use((req:Request,res:Response,next:NextFunction)=>{void oauth.handle(req,res,next).catch(()=>{if(!res.headersSent)res.status(500).json({error:'server_error',error_description:'授权服务暂不可用'});});});
  const mcp = app.get(McpService);
  app.use((req: Request, res: Response, next: NextFunction) => { void mcp.handle(req, res, next).catch(() => { if (!res.headersSent) res.status(500).json({ ok: false, error: { code: 'INTERNAL', message: 'MCP 请求处理失败', retryable: true, hint: '稍后重试' } }); }); });
  app.enableShutdownHooks();
  await app.init();
  return app;
}

if (require.main === module) {
  (async () => { const config = loadConfig(); const app = await createApp(config); await app.listen(config.port, config.host); })()
    .catch(() => { console.error('API startup failed: check configuration and database connectivity'); process.exitCode = 1; });
}
