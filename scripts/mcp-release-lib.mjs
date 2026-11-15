/** Restore MCP wiring on frozen deployed JS without replacing unrelated business startup. */
export function restoreMcpMain(baseline, compiled) {
 if (baseline.includes('McpOAuthHttp') || baseline.includes('McpCredentialsController')) throw Error('Baseline already has MCP wiring; review instead of patching twice');
 const imports = [...compiled.matchAll(/^const (\w+) = require\("(\.\/mcp[^"\n]*)"\);$/gm)];
 if (imports.length < 15) throw Error('Incomplete compiled MCP imports');
 const metadata = compiled.match(/controllers: \[([^\]]+)\], providers: \[([^\]]+)\]/);
 if (!metadata) throw Error('Compiled Nest metadata not found');
 const wanted = value => /\.Mcp\w+/.test(value);
 const controllers = metadata[1].split(',').map(v=>v.trim()).filter(wanted);
 const providers = metadata[2].split(',').map(v=>v.trim()).filter(wanted);
 if (controllers.length !== 2 || providers.length < 15) throw Error('Incomplete MCP Nest registration');
 let result = baseline;
 for (const [pattern, replacement] of [
  [/controllers: \[/g, 'controllers: ['+controllers.join(', ')+', '],
  [/providers: \[/g, 'providers: ['+providers.join(', ')+', ']
 ]) { if ([...result.matchAll(pattern)].length !== 1) throw Error('Unexpected deployed Nest metadata'); result=result.replace(pattern,replacement); }
 // MCP export delegates need the already imported reconciliation export service.
 if (!result.includes('require("./reconciliation-export")')) throw Error('Missing baseline reconciliation export module');
 const alias = result.match(/const (\w+) = require\("\.\/reconciliation-export"\)/)?.[1];
 if (!alias) throw Error('Unknown reconciliation export binding');
 if (!result.match(/providers: \[[^\]]*\.ReconciliationExportService/)) result=result.replace('providers: [',`providers: [${alias}.ReconciliationExportService, `);
 const middleware=compiled.match(/    const oauth = app\.get\([\s\S]*?(?=    app\.enableShutdownHooks\(\);)/)?.[0];
 if (!middleware || !middleware.includes('mcp.handle')) throw Error('Missing protocol middleware');
 if (result.split('    app.enableShutdownHooks();').length !== 2) throw Error('Unexpected shutdown hook');
 result=result.replace('    app.enableShutdownHooks();',middleware+'    app.enableShutdownHooks();');
 return imports.map(m=>m[0]).join('\n')+'\n'+result;
}
export function restoreMcpGateway(baseline, protocolLocations) {
 if (!baseline.includes('MCP_NOT_SUPPORTED') || !baseline.includes('OAUTH_NOT_SUPPORTED')) throw Error('Gateway baseline changed');
 const pattern=/ location \^~ \/mcp \{[^\n]+\}\n location \^~ \/oauth\/ \{[^\n]+\}\n location = \/\.well-known\/oauth-authorization-server \{[^\n]+\}\n location = \/\.well-known\/oauth-protected-resource \{[^\n]+\}\n/;
 if (!pattern.test(baseline) || !protocolLocations.includes('location = /mcp {') || !(protocolLocations.includes('proxy_pass http://api:3000;') || (protocolLocations.includes('set $protocol_upstream http://api:3000;') && protocolLocations.includes('proxy_pass $protocol_upstream;')))) throw Error('Unexpected protocol locations');
 return baseline.replace(pattern,protocolLocations+'\n');
}
