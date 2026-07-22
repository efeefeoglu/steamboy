#!/usr/bin/env python3
import json, math, os, subprocess, sys
from pathlib import Path
import cv2, numpy as np
from scenedetect import open_video, SceneManager
from scenedetect.detectors import AdaptiveDetector
from scoring import Scene, rank

source,out_dir,settings_raw=sys.argv[1:4]; settings=json.loads(settings_raw); out=Path(out_dir); ffmpeg=os.environ["FFMPEG_PATH"]
cap=cv2.VideoCapture(source)
if not cap.isOpened(): raise RuntimeError("Unsupported or corrupt video.")
fps=cap.get(cv2.CAP_PROP_FPS) or 30; frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); duration=frames/fps
if duration<=0: raise RuntimeError("Could not determine media duration.")
if duration>180: raise RuntimeError("Actual media duration exceeds the 3 minute limit.")

# PySceneDetect supplies robust adaptive cuts, while sampled OpenCV frames supply visual metrics.
video=open_video(source); manager=SceneManager(); manager.add_detector(AdaptiveDetector(adaptive_threshold=3.0,min_scene_len=max(5,int(fps*.35))))
manager.detect_scenes(video); boundaries=[(a.get_seconds(),b.get_seconds()) for a,b in manager.get_scene_list()]
if not boundaries: boundaries=[(0,duration)]

def sample_metrics(start,end):
    points=np.linspace(start,max(start,end-.05),min(10,max(2,int(end-start))))
    prev=None; motions=[]; visuals=[]; blacks=[]
    for t in points:
        cap.set(cv2.CAP_PROP_POS_MSEC,t*1000); ok,frame=cap.read()
        if not ok: continue
        small=cv2.resize(frame,(240,135)); gray=cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)
        blacks.append(float(np.mean(gray<18))*100); visuals.append(min(100,float(np.std(gray))*2.2))
        if prev is not None:
            flow=cv2.calcOpticalFlowFarneback(prev,gray,None,.5,2,12,2,5,1.1,0); motions.append(min(100,float(np.mean(np.linalg.norm(flow,axis=2)))*15))
        prev=gray
    return (np.mean(motions) if motions else 0,np.mean(visuals) if visuals else 0,np.mean(blacks) if blacks else 100)

# Decode mono PCM once and map RMS/peaks to scenes. FFmpeg failure means a silent/no-audio input.
audio=np.array([],dtype=np.int16)
try:
    raw=subprocess.run([ffmpeg,"-v","error","-i",source,"-vn","-ac","1","-ar","8000","-f","s16le","-"],capture_output=True,check=True,timeout=120).stdout
    audio=np.frombuffer(raw,dtype=np.int16)
except Exception: pass
def audio_score(a,b):
    if not len(audio): return 0
    x=audio[int(a*8000):int(b*8000)].astype(np.float32)/32768
    if not len(x): return 0
    rms=float(np.sqrt(np.mean(x*x))); peak=float(np.max(np.abs(x)))
    return min(100,rms*280+peak*35)

scenes=[]
for a,b in boundaries:
    motion,visual,black=sample_metrics(a,b); scenes.append(Scene(a,b,motion,audio_score(a,b),visual,black))
chosen=rank(scenes,settings["minDuration"],settings["maxDuration"],settings["resultCount"])
# Very low-cut videos need useful sliding windows rather than returning nothing.
if not chosen and duration>=settings["minDuration"]:
    end=min(duration,settings["maxDuration"]); m,v,b=sample_metrics(0,end); chosen=rank([Scene(0,end,m,audio_score(0,end),v,b)],settings["minDuration"],settings["maxDuration"],1)
clips=[]
for i,c in enumerate(chosen):
    name=f"highlight-{i+1}.mp4"; sheet=f"contact-{i+1}.jpg"
    subprocess.run([ffmpeg,"-y","-ss",str(c["start"]),"-i",source,"-t",str(c["duration"]),"-c:v","libx264","-preset","veryfast","-movflags","+faststart","-c:a","aac","-b:a","128k",str(out/name)],check=True,capture_output=True,timeout=180)
    thumbs=[]
    for t in np.linspace(c["start"],c["end"],6):
        cap.set(cv2.CAP_PROP_POS_MSEC,t*1000); ok,f=cap.read()
        if ok: thumbs.append(cv2.resize(f,(320,180)))
    if thumbs: cv2.imwrite(str(out/sheet),np.hstack(thumbs[:3]) if len(thumbs)<=3 else np.vstack([np.hstack(thumbs[:3]),np.hstack(thumbs[3:6])]))
    strengths=sorted(((c["motion"],"strong movement"),(c["audio"],"active audio"),(c["visual"],"visual variety")),reverse=True)
    clips.append({"file":name,"contactSheet":sheet if thumbs else "","start":c["start"],"end":c["end"],"duration":c["duration"],"score":c["score"],"metrics":{"motion":c["motion"],"audio":c["audio"],"visual":c["visual"],"selfContained":c["selfContained"]},"explanation":f"Selected for {strengths[0][1]} and {strengths[1][1]}, with low black-frame content. Scores are heuristic estimates."})
print(json.dumps({"duration":duration,"width":int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),"height":int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),"fps":fps,"hasAudio":bool(len(audio)),"clips":clips}))
