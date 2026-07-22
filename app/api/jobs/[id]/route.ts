import { getJob } from "@/lib/jobs";import { NextResponse } from "next/server";
export async function GET(_:Request,{params}:{params:Promise<{id:string}>}){const {id}=await params;if(!/^[\w-]+$/.test(id))return NextResponse.json({error:"Invalid job"},{status:400});const job=await getJob(id);return job?NextResponse.json(job):NextResponse.json({error:"Job not found"},{status:404})}
