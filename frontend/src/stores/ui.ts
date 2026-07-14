import { defineStore } from 'pinia'
export interface Toast{id:number;message:string;tone:'success'|'danger'|'info'}
export const useUiStore=defineStore('ui',{state:()=>({toasts:[] as Toast[],sidebarOpen:false}),actions:{toast(message:string,tone:Toast['tone']='info'){const id=Date.now();this.toasts.push({id,message,tone});window.setTimeout(()=>this.remove(id),3600)},remove(id:number){this.toasts=this.toasts.filter((item)=>item.id!==id)}}})
