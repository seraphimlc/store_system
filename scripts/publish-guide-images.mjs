/** Publish deterministic, privacy-free guide illustrations to the configured asset bucket. */
import {createHash} from 'node:crypto';
import {mkdir,writeFile} from 'node:fs/promises';
import {assetStorage} from './asset-storage.mjs';

const C={ink:'#263449',muted:'#536279',blue:'#2f5bea',line:'#dce2ea',paper:'#ffffff',soft:'#f4f6fa',green:'#16895f',amber:'#b67b12'};
const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
const rect=(x,y,w,h,fill=C.paper,stroke='none',r=18)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}" stroke-width="2"/>`;
const text=(x,y,value,size=36,color=C.ink,weight=500,anchor='start')=>`<text x="${x}" y="${y}" font-size="${size}" fill="${color}" font-weight="${weight}" text-anchor="${anchor}">${escape(value)}</text>`;
const line=(x1,y1,x2,y2,color=C.line,width=3)=>`<path d="M${x1},${y1} L${x2},${y2}" stroke="${color}" stroke-width="${width}" fill="none"/>`;
const dot=(x,y,color,r=13)=>`<circle cx="${x}" cy="${y}" r="${r}" fill="${color}" stroke="white" stroke-width="4"/>`;
const number=(x,y,n)=>`<circle cx="${x}" cy="${y}" r="25" fill="${C.blue}"/>${text(x,y+12,n,34,'white',650,'middle')}`;
const button=(x,y,w,label,primary=false)=>rect(x,y,w,88,primary?C.blue:C.paper,primary?C.blue:C.line,14)+text(x+w/2,y+57,label,38,primary?'white':C.ink,600,'middle');
const arrow=(x,y)=>`<path d="M${x},${y} h40 m-12,-12 l12,12 -12,12" fill="none" stroke="${C.muted}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>`;
function frame(title,body,ja){return `<svg xmlns="http://www.w3.org/2000/svg" width="960" height="760" viewBox="0 0 960 760" role="img" aria-labelledby="title"><title id="title">${escape(title)}</title><style>text{font-family:"Hiragino Sans GB","Hiragino Kaku Gothic ProN","Noto Sans CJK SC",sans-serif}</style>${rect(0,0,960,760,C.soft,'none',0)}${text(48,73,title,44,C.ink,650)}${text(48,121,ja?'操作イメージ・実際の画面に従ってください':'操作示意图 · 请以当前页面为准',29,C.muted)}${body}</svg>`;}
function illustration(kind,ja){
 const t=(zh,jp)=>ja?jp:zh;
 if(kind==='attendance')return frame(t('上班、下班，各打一次卡','出勤時と退勤時に打刻'),[
  rect(48,158,864,410,C.paper,C.line),text(88,219,t('出退勤记录','出退勤記録'),38,C.ink,650),text(88,267,t('今天 · 日本时间 05:00 换日','今日・日本時間05:00に日付切替'),30,C.muted),
  line(88,298,872,298),text(278,352,t('出勤','出勤'),32,C.muted,500,'middle'),text(682,352,t('退勤','退勤'),32,C.muted,500,'middle'),text(278,410,'--:--',52,C.ink,600,'middle'),text(682,410,'--:--',52,C.ink,600,'middle'),
  button(88,445,376,t('出勤打卡','出勤打刻'),true),button(496,445,376,t('退勤打卡','退勤打刻')),number(108,450,'1'),number(516,450,'2'),
  text(480,630,t('允许定位 → 等待提交 → 核对当天动态','位置取得を許可 → 送信 → 当日の履歴を確認'),34,C.ink,550,'middle'),text(480,694,t('定位成功后会自动记录，无需再次确认','位置取得後は自動で記録されます'),32,C.muted,500,'middle'),
 ].join(''),ja);
 if(kind==='choose')return frame(t('先选任务，再核对门店','タスクを選び、店舗を確認'),[
  ...[[48,t('批次','バッチ')],[360,t('片区','エリア')],[672,t('网格详情','グリッド詳細')]].map(([x,label])=>rect(x,164,240,88,C.paper,C.line)+text(x+120,221,label,35,C.ink,600,'middle')),arrow(302,208),arrow(614,208),
  rect(48,284,864,340,C.paper,C.line),text(88,348,t('巡店状态','巡回状態'),34,C.muted),rect(520,308,352,66,C.soft,C.line,10),text(696,353,t('未巡店','未実施'),34,C.ink,550,'middle'),line(88,400,872,400),
  rect(76,424,808,120,'#edf2ff','#b5c7fb',12),number(114,466,'1'),text(158,473,t('示意门店 A','サンプル店舗 A'),38,C.ink,600),text(158,518,t('核对名称和地址后选择','店名と住所を確認して選択'),31,C.muted),text(844,490,'›',54,C.blue,600,'middle'),
  text(88,591,t('示意门店 B','サンプル店舗 B'),36,C.muted),text(480,688,t('地图圆点、门店列表、名称／地址／编号搜索','地図・店舗一覧・名前／住所／番号で検索'),33,C.ink,550,'middle'),
 ].join(''),ja);
 if(kind==='map')return frame(t('看位置，也看门店状态','位置と店舗の状態を確認'),[
  rect(48,156,864,370,'#eaf0e8',C.line),
  `<path d="M60,430 C210,460 330,370 460,400 S720,460 900,370" fill="none" stroke="#c0deed" stroke-width="60"/>`,
  `<path d="M220,168 L370,520 M610,160 L510,520 M56,305 L902,254 M68,458 L896,435" fill="none" stroke="white" stroke-width="20"/>`,
  `<path d="M153,218 L660,194 L806,398 L295,477 Z" fill="${C.blue}" fill-opacity=".09" stroke="${C.blue}" stroke-width="3" stroke-dasharray="10 8"/>`,
  dot(280,278,C.amber,15),dot(670,253,C.green,15),dot(574,407,C.blue,21),dot(340,409,'#1376ef',13),text(340,466,t('我的位置','現在地'),29,C.ink,550,'middle'),
  rect(48,552,864,74,C.paper,C.line,12),dot(86,589,C.amber),text(113,602,t('未巡店','未実施'),32),dot(351,589,C.green),text(378,602,t('已巡店','実施済み'),32),dot(617,589,C.blue),text(644,602,t('选中门店','選択中'),32),
  ...[[48,t('地图全屏','全画面')],[342,t('查看全部','全体表示')],[636,t('回到我的位置','現在地へ')]].map(([x,label])=>rect(x,652,276,68,C.paper,C.line,12)+text(x+138,698,label,32,C.ink,550,'middle')),
 ].join(''),ja);
 return frame(t('到店核实，保存后再离开','現場で確認し、保存してから移動'),[
  rect(48,156,864,426,C.paper,C.line),text(88,215,t('门店状态','店舗状態'),35,C.ink,650),
  ...[[88,t('营业','営業')],[286,t('异常','異常')],[484,t('不确定','不明')],[682,t('关闭','閉店')]].map(([x,label],i)=>rect(x,240,180,68,i===0?'#edf2ff':C.paper,i===0?C.blue:C.line,12)+text(x+90,286,label,34,i===0?C.blue:C.ink,550,'middle')),
  text(88,367,t('作业结果','作業結果'),35,C.ink,650),
  ...[[88,t('完成','完了')],[354,t('待定','保留')],[620,t('未完成','未完了')]].map(([x,label],i)=>rect(x,392,244,68,i===0?'#edf2ff':C.paper,i===0?C.blue:C.line,12)+text(x+122,438,label,34,i===0?C.blue:C.ink,550,'middle')),
  rect(88,490,88,64,C.soft,C.line,8),`<path d="M102,540 l18,-18 13,10 18,-22 13,30 Z" fill="#b7c5d7"/>`,text(198,519,t('上传门店照片','店舗写真を追加'),34),text(198,558,t('最多 3 张，每张不超过 5MB','最大3枚・各5MBまで'),30,C.muted),
  button(48,606,864,t('保存','保存'),true),text(480,737,t('确认「巡店记录已保存」，再切换下一家','保存完了を確認してから次の店舗へ'),32,C.ink,550,'middle'),
 ].join(''),ja);
}

async function main(){
 process.loadEnvFile('.env');
 // Use exactly the project asset credentials, not an unrelated release environment override.
 const names=['STORE_FILES_OSS_ENDPOINT','STORE_FILES_OSS_BUCKET','STORE_FILES_CDN_BASE','OSS_ACCESS_KEY_ID','OSS_ACCESS_KEY_SECRET'];
 const env=Object.fromEntries(names.map(name=>[name,process.env[name]]));
 const base=env.STORE_FILES_CDN_BASE?.replace(/\/+$/,'');
 if(!base||new URL(base).protocol!=='https:')throw Error('An HTTPS STORE_FILES_CDN_BASE is required');
 const storage=await assetStorage(env),images={};
 const dir='data/guide-images';await mkdir(dir,{recursive:true});
 try{
  for(const kind of ['attendance','choose','map','visit']){
   images[kind]={};
   for(const lang of ['zh','ja']){
    const body=illustration(kind,lang==='ja'),sha256=createHash('sha256').update(body).digest('hex');
    const filename=`${kind}-${lang}-${sha256.slice(0,16)}.svg`,file=`${dir}/${filename}`,key=`store-next/guide/${filename}`;
    await writeFile(file,body);
    await storage.publish(key,file,{sha256,size:Buffer.byteLength(body)},'image/svg+xml');
    const url=`${base}/${key}`,response=await fetch(url);
    if(!response.ok||!response.headers.get('content-type')?.includes('image/svg+xml'))throw Error(`Public guide image validation failed (${kind}/${lang}): HTTP ${response.status}`);
    const downloaded=Buffer.from(await response.arrayBuffer());
    if(createHash('sha256').update(downloaded).digest('hex')!==sha256)throw Error(`Public guide image digest mismatch (${kind}/${lang})`);
    images[kind][lang]=url;
    console.log(JSON.stringify({image:`${kind}/${lang}`,url,bytes:Buffer.byteLength(body),verified:true}));
   }
  }
 }finally{storage.close();}
 await writeFile('apps/staff/src/guide-images.ts','// Immutable public guide assets. Credentials stay in the publication environment.\nexport const guideImages: Record<string, {zh: string; ja: string}> = '+JSON.stringify(images,null,2)+';\n');
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
