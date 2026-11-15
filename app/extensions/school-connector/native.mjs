import {HOST,Fault,boundedMessage} from './core.mjs';
// Browser errors are used only for classification. Never return or log raw text:
// an unexpected error could contain paths or other private browser details.
function browserFault(error) {
  const codes={
    'Specified native messaging host not found.':'HOST_NOT_FOUND',
    'Access to the specified native messaging host is forbidden.':'HOST_FORBIDDEN',
    'Failed to start native messaging host.':'HOST_START_FAILED',
    'Native host has exited.':'HOST_EXITED',
    'Error when communicating with the native messaging host.':'HOST_PROTOCOL_ERROR'
  };
  return Object.hasOwn(codes,error?.message)?codes[error.message]:'HOST_UNAVAILABLE';
}
export class NativeLink {
  constructor(api) {this.api=api;this.port=null;this.pending=new Map();}
  open() {
    if(this.port) return this.port;
    let port;try{port=this.api.runtime.connectNative(HOST);}catch(error){throw new Fault(browserFault(error));}
    this.port=port;
    port.onMessage.addListener(message=>{
      const pending=this.pending.get(message?.id);
      if(!pending) return;
      this.pending.delete(message.id);clearTimeout(pending.timer);
      if(message.version!==1||typeof message.ok!=='boolean') pending.reject(new Fault(['connect','check'].includes(pending.command)?'CONNECTION_OUTCOME_UNKNOWN':'HOST_UNAVAILABLE'));
      else if(!message.ok) {
        pending.reject(new Fault(message.error?.code||'INTERNAL_ERROR'));
        // A completed failure must not pin an old installed Host indefinitely.
        // The next explicit user action may open a new process; never replay here.
        if(!this.pending.size)this.close();
      }
      else pending.resolve(message.result);
    });
    port.onDisconnect.addListener(()=>{
      const code=browserFault(this.api.runtime.lastError);
      if(this.port===port)this.port=null;
      for(const [id,pending] of this.pending) {
        clearTimeout(pending.timer);
        pending.reject(new Fault(['connect','check'].includes(pending.command)?'CONNECTION_OUTCOME_UNKNOWN':code));
        this.pending.delete(id);
      }
    });
    return port;
  }
  async call(command,target,session) {
    const message=boundedMessage({version:1,id:crypto.randomUUID(),command,target,...(session?{session}:{})});
    const port=this.open();
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{
        this.pending.delete(message.id);
        reject(new Fault(['connect','check'].includes(command)?'CONNECTION_OUTCOME_UNKNOWN':'HOST_UNAVAILABLE'));
        if(!this.pending.size)this.close();
      },['connect','check'].includes(command)?185000:12000);
      this.pending.set(message.id,{resolve,reject,timer,command});
      try{port.postMessage(message);}catch{clearTimeout(timer);this.pending.delete(message.id);reject(new Fault(['connect','check'].includes(command)?'CONNECTION_OUTCOME_UNKNOWN':'HOST_UNAVAILABLE'));}
    });
  }
  close(){if(this.port){const p=this.port;this.port=null;p.disconnect();}}
}
