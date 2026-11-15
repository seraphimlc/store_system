import test from 'node:test';import assert from 'node:assert/strict';import {restoreMcpMain,restoreMcpGateway} from './mcp-release-lib.mjs';
test('MCP recovery preserves deployed startup and fails closed on changed baseline',()=>{
 const names=Array.from({length:17},(_,i)=>`McpService${i}`);const imports=names.map((n,i)=>`const mcp_${i} = require("./mcp-${i}");`).join('\n');
 const compiled=imports+'\ncontrollers: [mcp_0.McpCredentialsController, mcp_1.McpOAuthController], providers: ['+names.map((n,i)=>`mcp_${i}.${n}`).join(', ')+']\n    const oauth = app.get(mcp_1.McpOAuthHttp);\n    mcp.handle(req);\n    app.enableShutdownHooks();';
 const base='const recon = require("./reconciliation-export");\ncontrollers: [OldController], providers: [OldService]\n    app.enableShutdownHooks();\n    await warmMerchantSearch(db);';
 const result=restoreMcpMain(base,compiled);assert.ok(result.includes('OldController'));assert.ok(result.includes('OldService'));assert.ok(result.includes('await warmMerchantSearch(db)'));assert.ok(result.includes('recon.ReconciliationExportService'));assert.throws(()=>restoreMcpMain(result,compiled));assert.throws(()=>restoreMcpMain(base.replace('providers: [','other: ['),compiled));
});
test('gateway recovery replaces only the four explicit disabled protocol locations',()=>{
 const base='keep-before\n location ^~ /mcp { return 404 "MCP_NOT_SUPPORTED"; }\n location ^~ /oauth/ { return 404 "OAUTH_NOT_SUPPORTED"; }\n location = /.well-known/oauth-authorization-server { return 404; }\n location = /.well-known/oauth-protected-resource { return 404; }\nkeep-after';
 const locations=' location = /mcp { proxy_pass http://api:3000; }';assert.equal(restoreMcpGateway(base,locations),'keep-before\n'+locations+'\nkeep-after');assert.throws(()=>restoreMcpGateway('new-config',locations));
});

test('recovery includes the security response headers without replacing unrelated logging',()=>{
 const imports=Array.from({length:17},(_,i)=>`const mcp_${i} = require("./mcp-${i}");`).join('\n');
 const compiled=imports+'\ncontrollers: [mcp_0.McpCredentialsController, mcp_1.McpOAuthController], providers: ['+Array.from({length:17},(_,i)=>`mcp_${i}.McpService${i}`).join(', ')+']\n        res.setHeader(\'X-Frame-Options\', \'DENY\');\n        res.on(\'finish\', log);\n    const oauth = app.get(mcp_1.McpOAuthHttp);\n    mcp.handle(req);\n    app.enableShutdownHooks();';
 const baseline='const recon = require("./reconciliation-export");\ncontrollers: [OldController], providers: [OldService]\n        res.on(\'finish\', originalLog);\n    app.enableShutdownHooks();';
 const result=restoreMcpMain(baseline,compiled);assert.ok(result.includes("res.setHeader('X-Frame-Options', 'DENY')"));assert.ok(result.includes("res.on('finish', originalLog)"));
});
