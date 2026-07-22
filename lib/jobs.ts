import { put, list } from "@vercel/blob"; import type { Job } from "./types";
const path=(id:string)=>`jobs/${id}.json`;
export async function saveJob(job:Job){job.updatedAt=new Date().toISOString();await put(path(job.id),JSON.stringify(job),{access:"public",addRandomSuffix:false,contentType:"application/json",allowOverwrite:true});return job}
export async function getJob(id:string):Promise<Job|null>{const blobs=await list({prefix:path(id),limit:1});if(!blobs.blobs[0])return null;const r=await fetch(`${blobs.blobs[0].url}?t=${Date.now()}`,{cache:"no-store"});return r.ok?await r.json():null}
