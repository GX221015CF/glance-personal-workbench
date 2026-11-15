import {Fault} from './core.mjs';
// Bound browser APIs independently of timers running inside a school page.
// A late reply is ignored. It must never resume a cancelled connection chain.
export function browserStep(action,{signal,timeoutMs=10000}={}) {
  return new Promise((resolve,reject)=>{
    if(signal?.aborted){reject(new Fault('CANCELLED'));return;}
    let settled=false;
    const finish=(error,value)=>{
      if(settled)return;settled=true;clearTimeout(timer);
      signal?.removeEventListener('abort',abort);
      error?reject(error):resolve(value);
    };
    const abort=()=>finish(new Fault('CANCELLED'));
    const timer=setTimeout(()=>finish(new Fault('PAGE_CHECK_TIMEOUT')),timeoutMs);
    signal?.addEventListener('abort',abort,{once:true});
    Promise.resolve().then(()=>{
      if(signal?.aborted)throw new Fault('CANCELLED');
      return action();
    }).then(value=>finish(null,value),error=>finish(error));
  });
}
