import type {Store} from './types';

export function storeNavigationHref(store:Pick<Store,'lat'|'lon'|'snapshot'>):string|null{
 const valid=(lat:unknown,lon:unknown):lat is number=>typeof lat==='number'&&typeof lon==='number'&&Number.isFinite(lat)&&Number.isFinite(lon)&&Math.abs(lat)<=90&&Math.abs(lon)<=180;
 const destination=valid(store.lat,store.lon)?`${store.lat},${store.lon}`
  :valid(store.snapshot.lat,store.snapshot.lon)?`${store.snapshot.lat},${store.snapshot.lon}`
  :store.snapshot.address?.trim()||store.snapshot.name?.trim();
 return destination?'https://www.google.com/maps/dir/?'+new URLSearchParams({api:'1',destination}).toString():null;
}
