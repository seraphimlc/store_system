import {Controller,Get,Post,Inject,Req,Res} from '@nestjs/common';
import type {Request,Response} from 'express';
import {AuthService} from './auth';
import {BusinessError} from './errors';
/** Retired base asset IDs are never resolved into batch task IDs. No business writes. */
@Controller('legacy-work')
export class LegacyWorkController{
 constructor(@Inject(AuthService)private readonly auth:AuthService){}
 private async respond(req:Request,res:Response,write=false){
  try{
   // A retired form performs no action, so old CSRF/form tokens are intentionally not consumed.
   const {user}=await this.auth.require(req,{allowPasswordChange:true});
   if(write){res.status(410).json({detail:'legacy_task_retired'});return;}
   res.redirect(303,user.role==='admin'?'/admin/zone-tasks':'/my/work');
  }catch(error){
   if(error instanceof BusinessError&&error.code==='UNAUTHENTICATED'){res.redirect(302,'/login');return;}
   throw error;
  }
 }
 @Get('zones/:id')zone(@Req()req:Request,@Res()res:Response){return this.respond(req,res);}
 @Get('grids/:id')grid(@Req()req:Request,@Res()res:Response){return this.respond(req,res);}
 @Post('grids/:id/assign')assign(@Req()req:Request,@Res()res:Response){return this.respond(req,res,true);}
 @Post('grids/:id/visit')visit(@Req()req:Request,@Res()res:Response){return this.respond(req,res,true);}
}
