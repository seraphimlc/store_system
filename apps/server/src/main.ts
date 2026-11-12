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
import { ReconciliationExportController } from './reconciliation-export';
import { ConfigurationController, ConfigurationService } from './configuration';
import { ImportDeletionController, ImportDeletionService } from './import-deletion';
import { StoreMasterController, StoreMasterService } from './store-master';
import { PerformanceDailyController, PerformanceDailyService } from './performance-daily';
import { ReconciliationInsightController, ReconciliationInsightService } from './reconciliation-insight';
import { StoreAiController, StoreAiService } from './store-ai';
import { ReconciliationConfirmationController, ReconciliationConfirmationService } from './reconciliation-confirmation';
import { ApiExceptionFilter } from './errors';

export async function createApp(config: AppConfig) {
  @Module({ controllers: [OperationsLogsController,MerchantMapController,LegacyWorkController,ZoneAssetsController,AuthController, ReadController, EmployeesController, DailyReportsController, PlansController, MessagesController, ReportAnalysisController, ReportExportController, DashboardController, PayrollController, PayrollPaymentsController, SettlementController, SettlementJobsController, VisitImportsController, ReconciliationController, PayrollExportController, ReconciliationExportController, ConfigurationController, ReconciliationConfirmationController, ImportDeletionController, StoreMasterController, PerformanceDailyController, ReconciliationInsightController, StoreAiController,ImportReportController,TeamsController,StationTasksController,SelfReportController,StationExportController,StationAssetsController,StationRebuildController,RailImportController,StationSequenceController,BatchWorkController], providers: [OperationsLogsService,MerchantMapService,ZoneAssetsService,Database, AuthService, EmployeeService, DailyReportsService, ReportComparisonService, PlansService, MessagesService, ReportAnalysisService, DashboardService, PayrollService, PayrollPaymentsService, SettlementService, SettlementSyncService, SettlementJobsService,VisitImportsService,AdminUploadGuard,ReconciliationService,PayrollExportService,ConfigurationService,ReconciliationConfirmationService,ImportDeletionService,StoreMasterService,PerformanceDailyService,ReconciliationInsightService,StoreAiService,ImportReportService,TeamsService,StationTasksService,SelfReportService,StationExportService,StationAssetsService,StationRebuildService,RailImportService,StationSequenceService,BatchWorkService,BatchPhotoUploadGuard,{provide:'SETTLEMENT_JOBS',useExisting:SettlementJobsService}, { provide: CONFIG, useValue: config }] })
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
    res.on('finish', () => console.log(JSON.stringify({ event: 'request_end', requestId, method: req.method, path: req.path,
      status: res.statusCode, durationMs: Math.round(performance.now() - started), rss: process.memoryUsage().rss })));
    next();
  });
  app.enableShutdownHooks();
  await app.init();
  return app;
}

if (require.main === module) {
  (async () => { const config = loadConfig(); const app = await createApp(config); await app.listen(config.port, config.host); })()
    .catch(() => { console.error('API startup failed: check configuration and database connectivity'); process.exitCode = 1; });
}
