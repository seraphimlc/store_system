import { createHash } from 'node:crypto';
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import type { Plugin } from 'vite';

/** Public PWA endpoints belong to the app origin, never to ASSET_BASE_URL. */
export function staffPwa(): Plugin {
  let outDir = '';
  let building = false;
  return {
    name: 'staff-pwa',
    configResolved(config) { outDir = resolve(config.root, config.build.outDir); building = config.command === 'build'; },
    transformIndexHtml: {
      order: 'post',
      handler: () => [
        { tag: 'link', attrs: { rel: 'manifest', href: '/manifest.webmanifest' }, injectTo: 'head' },
        { tag: 'link', attrs: { rel: 'apple-touch-icon', href: '/pwa/apple-touch-icon.png' }, injectTo: 'head' },
        { tag: 'meta', attrs: { name: 'apple-mobile-web-app-title', content: '巡店' }, injectTo: 'head' },
        { tag: 'meta', attrs: { name: 'apple-mobile-web-app-capable', content: 'yes' }, injectTo: 'head' },
      ],
    },
    async closeBundle() {
      if (!building) return;
      const hash = createHash('sha256');
      async function walk(dir: string, prefix = ''): Promise<void> {
        for (const entry of (await readdir(dir, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
          const name = prefix + entry.name;
          if (entry.isDirectory()) await walk(join(dir, entry.name), name + '/');
          else if (name !== 'sw.js') hash.update(name).update(await readFile(join(dir, entry.name)));
        }
      }
      await walk(outDir);
      const path = join(outDir, 'sw.js');
      const source = await readFile(path, 'utf8');
      if (!source.includes('__STAFF_PWA_VERSION__')) throw new Error('Staff Worker version marker missing');
      // Hash the Worker template too: a policy-only change must also get a new version.
      const version = hash.update(source).digest('hex').slice(0, 20);
      await writeFile(path, source.replace('__STAFF_PWA_VERSION__', version));
    },
  };
}
