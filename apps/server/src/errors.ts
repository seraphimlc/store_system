import { ArgumentsHost, Catch, ExceptionFilter, HttpException } from '@nestjs/common';
import type { Request, Response } from 'express';
export class BusinessError extends HttpException {
  constructor(public readonly code: string, message: string, status: number) { super({ code, message }, status); }
}
@Catch()
export class ApiExceptionFilter implements ExceptionFilter {
  catch(error: unknown, host: ArgumentsHost) {
    const response = host.switchToHttp().getResponse<Response>();
    const request = host.switchToHttp().getRequest<Request>();
    if(response.headersSent||response.destroyed)return;
    const status = error instanceof HttpException ? error.getStatus() : 500;
    const payload = error instanceof BusinessError ? { code: error.code, message: error.message }
      : { code: status === 404 ? 'NOT_FOUND' : status === 500 ? 'INTERNAL' : 'BAD_REQUEST',
          message: status === 404 ? '接口不存在' : status === 500 ? '服务暂时不可用，请稍后重试' : '请求参数无效' };
    // Never log request bodies, database errors, cookies or credentials.
    if (status >= 500) console.error(JSON.stringify({ event: 'request_error', requestId: response.getHeader('X-Request-ID'), path: request.path, status }));
    response.status(status).json({ ...payload, requestId: response.getHeader('X-Request-ID') });
  }
}
