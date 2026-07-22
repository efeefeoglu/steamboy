export type Settings={minDuration:number;maxDuration:number;resultCount:number};
export type Clip={start:number;end:number;duration:number;score:number;url:string;contactSheetUrl?:string;explanation:string;metrics:{motion:number;audio:number;visual:number;selfContained:number}};
export type Job={id:string;sourceUrl:string;sourceName:string;sourceSize:number;createdAt:string;updatedAt:string;status:"queued"|"processing"|"complete"|"failed";stage:string;progress:number;settings:Settings;scoringMode:"heuristic"|"semantic";results?:Clip[];error?:string};
