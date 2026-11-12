/** Private offline CSV input, streamed without loading the merchant source into memory. */
import { createReadStream } from 'node:fs';
import { parseStream } from '@fast-csv/parse';
import { ZoneSnapshotError } from '../../../packages/domain/src/zone-snapshot';
import type { MerchantSourceRow } from '../../../packages/domain/src/zone-source';
export async function* merchantSourceCsv(path: string): AsyncGenerator<MerchantSourceRow> {
    const file = createReadStream(path, { encoding: 'utf8' });
    let gotHeaders = false;
    const parser = parseStream(file, { headers: headers => { gotHeaders = true; const names = new Set(headers); if (!names.has('place_id') || !names.has('lat') || (!names.has('lon') && !names.has('lng')))
            throw new ZoneSnapshotError('商户源必须包含place_id、lat、lon/lng'); return headers; }, ignoreEmpty: true, discardUnmappedColumns: true });
    file.on('error', error => parser.destroy(error));
    try {
        for await (const row of parser)
            yield row as MerchantSourceRow;
        if (!gotHeaders)
            throw new ZoneSnapshotError('商户源必须包含place_id、lat、lon/lng');
    }
    finally {
        parser.destroy();
        file.destroy();
    }
}
