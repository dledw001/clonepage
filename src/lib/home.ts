export type HomeSectionId = 'attention' | 'shortcuts' | 'continue' | 'reading' | 'feeds' | 'hacker-news';
export type HomeSettings = {
  show_clock: boolean;
  sections: { id: HomeSectionId; visible: boolean }[];
  previews: { reading: number; feeds: number; hacker_news: number; continue_reading: number };
  hacker_news: { view: 'front' | 'top' | 'discussed'; window: '24h' | '7d' | '30d' };
};
export type LayoutSnapshot = { revision: number; settings: HomeSettings };
export type LayoutState = LayoutSnapshot & { loaded: boolean; loading: boolean; busy: boolean; conflict: boolean; error: string };
export const sectionLabels: Record<HomeSectionId, string> = { attention:'Needs attention', shortcuts:'Your places', continue:'Continue reading', reading:'Reading list', feeds:'Feeds', 'hacker-news':'Hacker News' };
export function defaultHomeSettings(): HomeSettings {
  return { show_clock:true, sections: (['attention','shortcuts','continue','reading','feeds','hacker-news'] as HomeSectionId[]).map(id => ({id,visible:true})),
    previews:{reading:3,feeds:3,hacker_news:3,continue_reading:3}, hacker_news:{view:'top',window:'24h'} };
}
export function copySettings(settings: HomeSettings): HomeSettings { return JSON.parse(JSON.stringify(settings)); }
export function moveSection(settings: HomeSettings, id: HomeSectionId, direction: number): HomeSettings {
  const next = copySettings(settings), index = next.sections.findIndex(section => section.id === id), target = index + direction;
  if (index >= 0 && target >= 0 && target < next.sections.length) [next.sections[index], next.sections[target]] = [next.sections[target], next.sections[index]];
  return next;
}
export function homeSectionFromSearch(search: string): HomeSectionId | null {
  const value = new URLSearchParams(search).get('section');
  return value && Object.hasOwn(sectionLabels,value) ? value as HomeSectionId : null;
}

async function request<T>(fetcher: typeof fetch, controller: AbortController, path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetcher(path, { ...options, cache:'no-store', headers:{'Content-Type':'application/json',...options.headers},
    signal:AbortSignal.any([controller.signal,AbortSignal.timeout(20000)]) });
  if (!response.ok) {
    let message = 'The request could not be completed. Try again.';
    try { const value = await response.json(); if(typeof value.detail === 'string') message = value.detail; } catch { /* Keep fallback. */ }
    throw Object.assign(new Error(message), { conflict:response.status === 409 });
  }
  return response.json();
}

export class HomeLayoutStore {
  state: LayoutState = { settings:defaultHomeSettings(), revision:0, loaded:false, loading:false, busy:false, conflict:false, error:'' };
  private disposed = false;
  private controller = new AbortController();
  private sequence = 0;
  constructor(private publish: (state: LayoutState) => void, private fetcher: typeof fetch = (...args) => fetch(...args)) {}
  private update(changes: Partial<LayoutState>) { if(!this.disposed) { this.state={...this.state,...changes};this.publish(this.state); } }
  async refresh(force=false) {
    if(this.disposed || this.state.busy || this.state.loading || (this.state.conflict && !force)) return;
    const sequence=++this.sequence;
    this.update({loading:true});
    try {
      const value = await request<LayoutSnapshot>(this.fetcher,this.controller,'/api/home/layout');
      if(sequence === this.sequence) this.update({...value,loaded:true,error:'',conflict:false});
    } catch(error) { if(sequence === this.sequence) this.update({error:error instanceof Error ? error.message : 'Could not load your homepage layout.'}); }
    finally { if(sequence === this.sequence) this.update({loading:false}); }
  }
  async save(settings: HomeSettings, revision = this.state.revision) {
    if(this.disposed || this.state.busy || !this.state.loaded || this.state.conflict) return false;
    this.sequence++;
    this.update({busy:true,loading:false,error:''});
    try {
      const value = await request<LayoutSnapshot>(this.fetcher,this.controller,'/api/home/layout',{
        method:'PUT',body:JSON.stringify({revision,settings}) });
      this.update({...value,loaded:true,conflict:false});return true;
    } catch(error) {
      this.update({error:error instanceof Error ? error.message : 'Could not save your layout.',conflict:Boolean((error as {conflict?:boolean})?.conflict)});return false;
    } finally { this.update({busy:false}); }
  }
  dispose() { this.disposed=true;this.sequence++;this.controller.abort(); }
}

export type AttentionItem = { id:string;kind:'service'|'update'|'check'|'feed'|'copy';title:string;detail:string;href:string;count:number };
export type AttentionSnapshot = { items:AttentionItem[];counts:Record<AttentionItem['kind'],number>;total:number;checked_at:string };
export type ContinueItem = { id:string;title:string;format:'pdf'|'html';label:string;updated_at:string };
export type ContinueSnapshot = { items:ContinueItem[] };
export type HomeDataState<T> = { data:T|null;loading:boolean;error:string };
export class HomeDataStore<T> {
  state:HomeDataState<T>={data:null,loading:false,error:''};
  private disposed=false;
  private queued=false;
  private controller=new AbortController();
  constructor(private path:string,private publish:(state:HomeDataState<T>)=>void,private fetcher:typeof fetch=(...args)=>fetch(...args)){}
  private update(changes:Partial<HomeDataState<T>>) { if(!this.disposed) {this.state={...this.state,...changes};this.publish(this.state);} }
  async refresh() {
    if(this.disposed)return;
    if(this.state.loading){this.queued=true;return;}
    this.update({loading:true});
    try {this.update({data:await request<T>(this.fetcher,this.controller,this.path),error:''});}
    catch(error){this.update({error:error instanceof Error?error.message:'Could not refresh this section.'});}
    finally {this.update({loading:false});if(this.queued&&!this.disposed){this.queued=false;void this.refresh();}}
  }
  dispose(){this.disposed=true;this.controller.abort();}
}
