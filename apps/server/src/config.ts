export interface AppConfig {
  databaseUrl: string;
  oauthTrustedRedirectUris?: string[];
  mcpPublicUrl?: string;
  mcpAuthorizationUiUrl?: string;
  sessionSecret: string;
  cookieSecure: boolean;
  port: number;
  host: string;
}
export function loadConfig(env = process.env): AppConfig {
  const databaseUrl = env.NEXT_DATABASE_URL ?? '';
  const sessionSecret = env.NEXT_SESSION_SECRET ?? '';
  const parsed = new URL(databaseUrl);
  if (!['postgres:', 'postgresql:'].includes(parsed.protocol)) throw new Error('NEXT_DATABASE_URL must use PostgreSQL');
  if (sessionSecret.length < 32) throw new Error('NEXT_SESSION_SECRET must be at least 32 characters');
  const port = Number(env.NEXT_PORT ?? 3000);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('NEXT_PORT is invalid');
  const localHttp=env.NEXT_LOCAL_HTTP==='true'&&(parsed.pathname.endsWith('_refactor_p1')||env.NEXT_LOCAL_BASELINE==='true'&&parsed.pathname==='/store_settle'&&['localhost','127.0.0.1','host.docker.internal','[::1]'].includes(parsed.hostname));
  if (env.NODE_ENV === 'production' && env.NEXT_COOKIE_SECURE !== 'true' && !localHttp) throw new Error('Production requires secure cookies');
  const oauthTrustedRedirectUris: string[] = env.NEXT_OAUTH_TRUSTED_REDIRECT_URIS ? JSON.parse(env.NEXT_OAUTH_TRUSTED_REDIRECT_URIS) : [];
  if (!Array.isArray(oauthTrustedRedirectUris) || oauthTrustedRedirectUris.some(uri => typeof uri !== 'string' || !uri || uri.length>512)) throw new Error('NEXT_OAUTH_TRUSTED_REDIRECT_URIS must be a JSON array of exact callback URIs');
  const mcpPublicUrl = env.NEXT_MCP_PUBLIC_URL;
  if (mcpPublicUrl) {
    const url = new URL(mcpPublicUrl);
    if (url.pathname !== '/mcp' || url.search || url.hash || url.username || url.password || !['http:', 'https:'].includes(url.protocol) || env.NODE_ENV === 'production' && url.protocol !== 'https:' && !localHttp) throw new Error('NEXT_MCP_PUBLIC_URL must be the public /mcp URL (HTTPS in production)');
  }
  const mcpAuthorizationUiUrl=env.NEXT_MCP_AUTHORIZATION_UI_URL;
  if(mcpAuthorizationUiUrl){const ui=new URL(mcpAuthorizationUiUrl);if(ui.pathname!=='/admin/mcp-authorize'||ui.search||ui.hash||ui.username||ui.password||!(ui.protocol==='https:'||ui.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(ui.hostname))||env.NODE_ENV==='production'&&ui.protocol!=='https:'&&!localHttp)throw new Error('NEXT_MCP_AUTHORIZATION_UI_URL must be an HTTPS /admin/mcp-authorize URL or local development URL');}
  return { databaseUrl, oauthTrustedRedirectUris, sessionSecret, mcpPublicUrl,mcpAuthorizationUiUrl, cookieSecure: env.NEXT_COOKIE_SECURE === 'true', port, host: env.NEXT_HOST ?? '127.0.0.1' };
}
export const CONFIG = Symbol('CONFIG');
