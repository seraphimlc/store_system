/** Shared by local Gateway assembly and future explicitly scoped production preparation. */
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
export const staffPwaPaths = ['sw.js', 'manifest.webmanifest', 'pwa/offline.html', 'pwa/icon-192.png', 'pwa/icon-512.png', 'pwa/apple-touch-icon.png', 'pwa/sw-disable.js'];
export const isStaffPwaPath = name => staffPwaPaths.includes(name);
export async function stageStaffPwa(source, target, { optional = false, expected } = {}) {
  const files = [];
  for (const path of staffPwaPaths) {
    let bytes;
    try { bytes = await readFile(join(source, path)); }
    catch (error) { if (optional && !expected?.length && error.code === 'ENOENT' && files.length === 0 && path === 'sw.js') return []; throw error; }
    const entry = { path, size: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') };
    const previous = expected?.find(file => file.path === path);
    if (expected && (!previous || previous.sha256 !== entry.sha256 || previous.size !== entry.size)) throw new Error('Previous staff PWA checksum mismatch: ' + path);
    await mkdir(dirname(join(target, path)), { recursive: true });
    await writeFile(join(target, path), bytes);
    files.push(entry);
  }
  return files;
}
export async function verifyStaffPwa(root, entries = []) {
  if (!entries.length) return; // Releases predating PWA remain deployable.
  if (entries.length !== staffPwaPaths.length || new Set(entries.map(file => file.path)).size !== staffPwaPaths.length) throw new Error('Incomplete staff PWA manifest');
  for (const entry of entries) {
    if (!isStaffPwaPath(entry.path)) throw new Error('Unexpected staff PWA file');
    const bytes = await readFile(join(root, entry.path));
    if (bytes.length !== entry.size || createHash('sha256').update(bytes).digest('hex') !== entry.sha256) throw new Error('Staff PWA checksum mismatch: ' + entry.path);
  }
}
export function staffPwaGatewayLocations() {
  return ` location = /sw.js { try_files $uri =404; default_type application/javascript; add_header Cache-Control "no-cache" always; add_header X-Content-Type-Options nosniff always; }
 location = /manifest.webmanifest { try_files $uri =404; types { } default_type application/manifest+json; add_header Cache-Control "no-cache" always; add_header X-Content-Type-Options nosniff always; }
 location ^~ /pwa/ { try_files $uri =404; add_header Cache-Control "no-cache" always; add_header X-Content-Type-Options nosniff always; }
`;
}
