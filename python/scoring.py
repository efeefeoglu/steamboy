from dataclasses import dataclass

@dataclass
class Scene:
    start: float
    end: float
    motion: float
    audio: float
    visual: float
    black: float = 0.0

def candidates(scenes, minimum=8, maximum=30):
    """Combine adjacent shots into every valid candidate window."""
    out=[]
    for i in range(len(scenes)):
        for j in range(i,len(scenes)):
            duration=scenes[j].end-scenes[i].start
            if duration>maximum: break
            if duration>=minimum:
                part=scenes[i:j+1]; weights=[s.end-s.start for s in part]; total=sum(weights)
                avg=lambda name:sum(getattr(s,name)*w for s,w in zip(part,weights))/total
                out.append({"start":scenes[i].start,"end":scenes[j].end,"duration":duration,
                    "motion":avg("motion"),"audio":avg("audio"),"visual":avg("visual"),"black":avg("black")})
    return out

def score(candidate, minimum=8, maximum=30):
    duration=candidate["duration"]
    duration_fit=max(0,1-abs(duration-(minimum+maximum)/2)/((maximum-minimum)/2+1))
    self_contained=min(100,45+duration_fit*35+min(candidate["visual"],candidate["motion"])*.2)
    overall=(candidate["motion"]*.34+candidate["audio"]*.24+candidate["visual"]*.27+self_contained*.15-candidate["black"]*.7)
    return max(0,min(100,overall)),self_contained

def remove_overlaps(ranked, threshold=.55, limit=5):
    selected=[]
    for item in ranked:
        if all(max(0,min(item["end"],x["end"])-max(item["start"],x["start"]))/min(item["duration"],x["duration"])<threshold for x in selected):
            selected.append(item)
            if len(selected)>=limit: break
    return selected

def rank(scenes, minimum=8, maximum=30, limit=5):
    items=candidates(scenes,minimum,maximum)
    for item in items:item["score"],item["selfContained"]=score(item,minimum,maximum)
    return remove_overlaps(sorted(items,key=lambda x:x["score"],reverse=True),limit=limit)
