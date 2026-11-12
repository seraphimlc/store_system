import {railRound} from './rail-geometry';
export const MERCHANT_POINT_LIMIT=1000,MERCHANT_POINT_ZOOM=17,MAX_MERCATOR_LAT=85.05112878;
export interface MerchantView{west:number;south:number;east:number;north:number;zoom:number}
export function merchantView(input:MerchantView){
 const {west,south,east,north,zoom}=input;
 if([west,south,east,north,zoom].some(value=>typeof value!=='number'||!Number.isFinite(value))||west< -180||west>180||east< -180||east>180||west===east||south< -MAX_MERCATOR_LAT||north>MAX_MERCATOR_LAT||south>=north||zoom<0||zoom>22)throw Error('INVALID_MERCHANT_VIEW');
 const y=(lat:number)=>(1-Math.asinh(Math.tan(lat*Math.PI/180))/Math.PI)/2;
 const width=(east>west?east-west:east-west+360)/360,height=y(south)-y(north),bucketZoom=Math.max(0,Math.min(Math.trunc(zoom)+1,22,Math.floor(Math.log2(20/Math.max(width,height)))));
 return {...input,bucketZoom,scale:2**bucketZoom};
}
export function merchantSummary(count:number,covered:number){return {merchant_count:count,covered_count:covered,coverage_pct:count?railRound(covered*100/count,1):0};}
