"use client";
import { upload } from "@vercel/blob/client";
import { FormEvent, useEffect, useRef, useState } from "react";
import type { Job } from "@/lib/types";

const MAX = 250 * 1024 * 1024;
export default function Home() {
  const [file,setFile]=useState<File>(); const [job,setJob]=useState<Job>(); const [progress,setProgress]=useState(0);
  const [busy,setBusy]=useState(false); const [error,setError]=useState(""); const [min,setMin]=useState(8); const [max,setMax]=useState(30); const [count,setCount]=useState(5);
  const timer=useRef<ReturnType<typeof setInterval>>();
  async function load(id:string){const r=await fetch(`/api/jobs/${id}`,{cache:"no-store"});if(r.ok){const j=await r.json();setJob(j);if(["complete","failed"].includes(j.status)&&timer.current)clearInterval(timer.current)}}
  useEffect(()=>{const id=localStorage.getItem("steamboy-job");if(id){load(id);timer.current=setInterval(()=>load(id),2500)}return()=>clearInterval(timer.current)},[]);
  async function submit(e:FormEvent){e.preventDefault();setError("");if(job&&!["complete","failed"].includes(job.status))return setError("This browser already has an active analysis. Wait for it to finish.");if(!file)return setError("Choose a video first.");if(file.size>MAX)return setError("Video exceeds the 250 MB MVP limit.");
    setBusy(true);try{const blob=await upload(file.name,file,{access:"public",handleUploadUrl:"/api/upload",onUploadProgress:p=>setProgress(Math.round(p.percentage))});
      const r=await fetch("/api/jobs",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({sourceUrl:blob.url,sourceName:file.name,sourceSize:file.size,settings:{minDuration:min,maxDuration:max,resultCount:count}})});const data=await r.json();if(!r.ok)throw new Error(data.error);localStorage.setItem("steamboy-job",data.id);await load(data.id);timer.current=setInterval(()=>load(data.id),2500);
    }catch(e){setError(e instanceof Error?e.message:"Unable to start analysis.")}finally{setBusy(false)}}
  return <main><header><span className="eyebrow">VERCEL VIDEO MVP</span><h1>Find the moments worth sharing.</h1><p>Upload a gameplay clip or trailer. SteamBoy finds energetic, visually interesting highlights—without requiring an AI key.</p></header>
    <section className="panel"><form onSubmit={submit}><label className="drop"><strong>{file?file.name:"Choose an MP4, MOV, WebM, or MKV"}</strong><span>{file?`${(file.size/1048576).toFixed(1)} MB`:"Up to 3 minutes · 250 MB"}</span><input type="file" accept="video/mp4,video/quicktime,video/webm,video/x-matroska" onChange={e=>setFile(e.target.files?.[0])}/></label>
      <div className="settings"><label>Minimum seconds<input type="number" min="4" max="20" value={min} onChange={e=>setMin(+e.target.value)}/></label><label>Maximum seconds<input type="number" min="8" max="30" value={max} onChange={e=>setMax(+e.target.value)}/></label><label>Results<input type="number" min="1" max="10" value={count} onChange={e=>setCount(+e.target.value)}/></label></div>
      {busy&&<div className="bar"><i style={{width:`${progress}%`}}/><span>{progress<100?`Uploading ${progress}%`:"Creating durable workflow…"}</span></div>}<button disabled={busy}>{busy?"Working…":"Upload & analyze"}</button>{error&&<p className="error">{error}</p>}</form></section>
    {job&&<section className="results"><div className="status"><div><span className={`dot ${job.status}`}/><strong>{job.status==="complete"?"Highlights ready":job.status==="failed"?"Analysis failed":"Analysis in progress"}</strong><p>{job.stage}</p></div><b>{job.progress}%</b></div>{job.error&&<p className="error">{job.error}</p>}
      {job.results?.map((clip,i)=><article className="clip" key={clip.url}><video controls preload="metadata" src={clip.url}/><div><span className="rank">#{i+1} HIGHLIGHT · {job.scoringMode==="heuristic"?"HEURISTIC ESTIMATE":"AI + HEURISTIC"}</span><h2>{clip.start.toFixed(1)}s – {clip.end.toFixed(1)}s <small>{clip.duration.toFixed(1)} seconds</small></h2><div className="score"><strong>{Math.round(clip.score)}</strong><span>overall<br/>score</span></div><p>{clip.explanation}</p><div className="metrics">{Object.entries(clip.metrics).map(([k,v])=><label key={k}><span>{k.replace("selfContained","self-contained")}</span><b>{Math.round(v)}</b><i><em style={{width:`${v}%`}}/></i></label>)}</div><a className="download" href={clip.url} download>Download MP4</a></div></article>)}</section>}
    <footer>Files are stored in Vercel Blob. Temporary processing files are discarded after every run.</footer></main>
}
