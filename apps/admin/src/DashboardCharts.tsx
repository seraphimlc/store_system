import type {MonthlyRow} from './Overview';
const nf=new Intl.NumberFormat('zh-CN');
function Bars({title,rows,series}:{title:string;rows:MonthlyRow[];series:{label:string;color:string;value:(row:MonthlyRow)=>number}[]}){
 const max=Math.max(1,...rows.map(row=>series.reduce((sum,s)=>sum+s.value(row),0))),w=460,h=240,left=62,bottom=205,plot=175,step=(w-left-15)/Math.max(1,rows.length),bar=Math.min(48,step*.58);
 return <figure className="dashboard-chart"><figcaption>{title}</figcaption><svg role="img" aria-label={title} viewBox={`0 0 ${w} ${h}`}>
 {Array.from({length:5},(_,i)=>{const y=bottom-i*plot/4;return <g key={i}><line x1={left} x2={w-15} y1={y} y2={y} stroke="var(--border)"/><text x={left-8} y={y+4} textAnchor="end">{nf.format(Math.round(max*i/4))}</text></g>;})}
 {rows.map((row,i)=>{let used=0;return <g key={row.month}>{series.map(s=>{const value=s.value(row),height=value/max*plot,y=bottom-used-height;used+=height;return <rect key={s.label} x={left+step*(i+.5)-bar/2} y={y} width={bar} height={height} fill={s.color}><title>{row.month} · {s.label}: {nf.format(value)}</title></rect>;})}<text x={left+step*(i+.5)} y={225} textAnchor="middle">{row.month.slice(5)}月</text></g>;})}</svg>
 <div className="chart-legend">{series.map(s=><span key={s.label}><i style={{background:s.color}}/>{s.label}</span>)}</div></figure>;
}
export function DashboardCharts({rows,current,language}:{rows:MonthlyRow[];current:MonthlyRow;language:string}){
 const t=(zh:string,ja:string)=>language==='ja'?ja:zh;const ratio=current.records?current.p2/current.records:0;
 return <div className="dashboard-charts">
 <Bars title={t('总点数（月度）','合計ポイント（月別）')} rows={rows} series={[{label:t('总点数','ポイント'),color:'#5685d6',value:r=>r.points}]}/>
 <Bars title={t('工资总额（円，月度）','給与総額（円・月別）')} rows={rows} series={[{label:t('工资总额','給与総額'),color:'#3b8b68',value:r=>r.amount}]}/>
 <Bars title={t('有效店数（月度）','有効店舗数（月別）')} rows={rows} series={[{label:t('有效店','有効店舗'),color:'#b4763a',value:r=>r.records}]}/>
 <Bars title={t('店铺结构演变（1点 / 2点）','店舗構成（1 / 2ポイント）')} rows={rows} series={[{label:t('1点店','1ポイント店舗'),color:'#9bb4d7',value:r=>r.p1},{label:t('2点店','2ポイント店舗'),color:'#9163d1',value:r=>r.p2}]}/>
 <Bars title={t('人员变化（员工数 / 新增 / 未出现）','スタッフ数・初出・データなし')} rows={rows} series={[{label:t('员工数','スタッフ数'),color:'#5685d6',value:r=>r.employees},{label:t('新增','初出'),color:'#3b8b68',value:r=>r.newEmployees??0},{label:t('未出现','データなし'),color:'#bd4959',value:r=>r.goneEmployees??0}]}/>
 <figure className="dashboard-chart"><figcaption>{t('所选月店铺结构（1点 / 2点）','対象月の店舗構成')}</figcaption><svg role="img" aria-label={t('2点店占比','2ポイント店舗比率')+' '+(ratio*100).toFixed(1)+'%'} viewBox="0 0 460 240"><circle cx="230" cy="120" r="83" fill="none" stroke="#dce3eb" strokeWidth="36"/><circle cx="230" cy="120" r="83" fill="none" stroke="#9163d1" strokeWidth="36" strokeDasharray={`${ratio*521.5} 521.5`} transform="rotate(-90 230 120)"/><text x="230" y="116" textAnchor="middle">{current.month}</text><text x="230" y="139" textAnchor="middle">{nf.format(current.records)} {t('家','店舗')}</text></svg><div className="chart-legend"><span><i style={{background:'#dce3eb'}}/>{t('1点店','1ポイント店舗')} {nf.format(current.p1)}</span><span><i style={{background:'#9163d1'}}/>{t('2点店','2ポイント店舗')} {nf.format(current.p2)}</span></div></figure>
 </div>;
}
