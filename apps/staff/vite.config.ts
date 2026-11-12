import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwind from '@tailwindcss/vite';
export default defineConfig({ base: process.env.ASSET_BASE_URL || '/', plugins: [react(), tailwind(), { name: 'admin-dev-entry', apply: 'serve', configureServer(server) { server.middlewares.use((req, res, next) => { if (req.url === '/admin' || req.url?.startsWith('/admin/')) { res.writeHead(302, { Location: 'http://127.0.0.1:8001' + req.url }); res.end(); } else next(); }); } }], server: { port: 8002, strictPort: true, proxy: { '/api': process.env.NEXT_DEV_API_TARGET || 'http://127.0.0.1:3000' } }, build: { sourcemap: false } });
