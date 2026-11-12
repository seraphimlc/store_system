import { createRequire } from 'node:module';
import { chromium } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
const require = createRequire(new URL('../apps/server/package.json', import.meta.url));
const { Pool } = require('pg'); const { hash } = require('@node-rs/argon2');
const db = new Pool({ connectionString: process.env.NEXT_TEST_DATABASE_URL });
if (!new URL(process.env.NEXT_TEST_DATABASE_URL).pathname.endsWith('_refactor_p1')) throw new Error('Isolated database required');
const usernamePrefix = 'preview-p1-'; const password = 'Preview-p1-2026';
let browser;
try {
 const pw = await hash(password);
 await db.query(`INSERT INTO users(username,password_hash,display_name,role,is_active,status,must_change_password,lang,created_at) VALUES
 ('preview-p1-admin',$1,'验收管理员','admin',true,'active',false,'zh',now()),
 ('preview-p1-staff',$1,'验收员工','staff',true,'active',false,'zh',now())`, [pw]);
 browser = await chromium.launch({ headless: true, ...(process.env.NEXT_BROWSER_EXECUTABLE ? { executablePath: process.env.NEXT_BROWSER_EXECUTABLE } : { channel: 'chrome' }) });
 const errors=[]; const results={};
 const desktop = await browser.newContext({ viewport: { width: 1440, height: 950 } });
 const admin = await desktop.newPage(); admin.on('pageerror', e=>errors.push(e.message));
 await admin.goto((process.env.NEXT_BROWSER_BASE_URL ? process.env.NEXT_BROWSER_BASE_URL + '/admin/' : 'http://127.0.0.1:8001/admin/'));
 await admin.getByLabel('账号', { exact: true }).fill('preview-p1-admin');
 await admin.getByLabel('密码', { exact: true }).fill(password);
 await admin.getByRole('button', { name: '登录', exact: true }).click();
 await admin.getByRole('heading', { name: '工作概览', exact: true }).waitFor();
 await admin.getByRole('heading', { name: '暂无可查看的巡店批次' }).waitFor();
 results.admin={ heading:await admin.locator('h1').innerText(), summaryFields:await admin.locator('.summary-strip > div').count(), overflow:await admin.evaluate(()=>document.documentElement.scrollWidth>innerWidth) };
 await admin.getByRole('button', { name: '中文 / 日本語' }).click();
 await admin.getByRole('heading', { name: '業務概要', exact: true }).waitFor();
 results.admin.japanese=true;
 await admin.getByRole('button', { name: 'ログアウト', exact: true }).click();
 await admin.getByRole('heading', { name: '管理画面にログイン', exact: true }).waitFor();
 const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile:true, hasTouch:true });
 const staff=await mobile.newPage(); staff.on('pageerror', e=>errors.push(e.message));
 await staff.goto((process.env.NEXT_BROWSER_BASE_URL ? process.env.NEXT_BROWSER_BASE_URL + '/' : 'http://127.0.0.1:8002/'));
 await staff.getByLabel('账号', { exact:true }).fill('preview-p1-staff');
 await staff.getByLabel('密码', { exact:true }).fill(password);
 await staff.getByRole('button',{name:'登录',exact:true}).click();
 await staff.getByRole('heading',{name:'我的巡店批次',exact:true}).waitFor();
 await staff.getByText('暂无可查看的巡店批次',{exact:true}).waitFor();
 results.staff={ heading:await staff.locator('h1').innerText(), overflow:await staff.evaluate(()=>document.documentElement.scrollWidth>innerWidth), tabs:await staff.locator('.adm-tab-bar-item').count() };
 await staff.getByRole('button',{name:'中文 / 日本語'}).click();
 await staff.getByRole('heading',{name:'自分の巡回バッチ',exact:true}).waitFor();
 await staff.getByText('マイアカウント',{exact:true}).last().click();
 await staff.getByText('パスワード変更',{exact:true}).click();
 await staff.getByLabel('現在のパスワード',{exact:true}).waitFor();
 results.staff.popup=true;
 await staff.getByRole('button',{name:'閉じる',exact:true}).click();
 await staff.getByRole('button',{name:'ログアウト',exact:true}).click();
 await staff.getByRole('heading',{name:'巡回ワークスペース',exact:true}).waitFor();
 results.pageErrors=errors;
 if(errors.length || results.admin.overflow || results.staff.overflow)throw new Error(JSON.stringify(results));
 await writeFile('data/refactor/p1-browser.json',JSON.stringify(results,null,2)+'\n');
 results.target = process.env.NEXT_BROWSER_BASE_URL ?? 'vite-dev';
 console.log(JSON.stringify(results,null,2));
} finally {
 if(browser)await browser.close();
 await db.query('DELETE FROM users WHERE username LIKE $1',[usernamePrefix+'%']);
 await db.end();
}
