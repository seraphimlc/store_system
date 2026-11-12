import {spawnSync} from 'node:child_process';
import {dirname,resolve,delimiter} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
/** Run archived Python solely for legacy parity tools; application runtime never imports it. */
export function spawnLegacyPython(args,options={}){
 const env={...process.env,...options.env};env.PYTHONPATH=[resolve(root,'legacy'),env.PYTHONPATH].filter(Boolean).join(delimiter);
 return spawnSync(resolve(root,'.venv/bin/python'),args,{...options,env});
}
