import json, math, array
import numpy as np
import pyarrow.parquet as pq
from collections import defaultdict

ANN=math.sqrt(365*24*3600); SQ2=math.sqrt(2.0)
def phi(x): return 0.5*(1+math.erf(x/SQ2))
def load_btc(path):
    f=open(path,'rb'); h=json.loads(f.readline()); a=array.array('d'); a.frombytes(f.read())
    return int(h['start']), np.frombuffer(a,dtype=np.float64).copy()
def realized_vol(closes,i,win=600,floor=0.25,cap=3.0):
    a=max(1,i-win); seg=np.asarray(closes[a:i+1],dtype=float)
    seg=seg[np.isfinite(seg)]
    if len(seg)<3: return floor
    r=np.diff(np.log(seg)); r=r[np.isfinite(r)]
    if len(r)<2: return floor
    return float(min(max(math.sqrt(float(np.mean(r*r)))*ANN,floor),cap))
def p_twap(px,tgt,s,vol,seen_sum=0.0):
    if s<=0: return 1.0 if px>tgt else 0.0
    sig=vol*px/ANN
    if s>=60.0:
        sd=sig*math.sqrt(max(s-40.0,1e-9))/60.0; mean=px
    else:
        sd=sig*math.sqrt(s**3/3.0)/60.0; mean=(seen_sum+px*s)/60.0
    if sd<=1e-12: return 1.0 if mean>tgt else 0.0
    return phi((mean-tgt)/sd)
def side_fees(fills):
    gross=sum(p*q for p,q in fills); prot=sum(0.07*q*p*(1-p) for p,q in fills)
    return round(prot+1e-12,5)+round(gross*0.01+1e-12,5)
def walk_buy(levels,cash):
    fills=[]; rem=cash
    for p,q in levels:
        if not (0<p<1) or q<=0: continue
        rate=p*1.01+0.07*p*p*(1-p)
        take=min(q, rem/rate)
        if take<=1e-9: break
        fills.append((p,take)); rem-=p*take
        if take<q-1e-9: break
    while fills:
        g=sum(p*q for p,q in fills); f=side_fees(fills)
        if g+f<=cash+1e-9: break
        p,q=fills[-1]; rate=p*1.01+0.07*p*p*(1-p)
        q2=max(0.0,q-(g+f-cash)/rate-1e-6)
        if q2<=1e-9: fills.pop()
        else: fills[-1]=(p,q2)
    if not fills: return 0.0,0.0,0.0
    return sum(q for _,q in fills), sum(p*q for p,q in fills), side_fees(fills)
def walk_sell(levels,shares):
    rem=shares; fills=[]
    for p,q in levels:
        if not (0<p<1) or q<=0: continue
        take=min(rem,q); fills.append((p,take)); rem-=take
        if rem<=1e-9: break
    if not fills: return 0.0,0.0,0.0
    return shares-rem, sum(p*q for p,q in fills), side_fees(fills)

class Side:
    __slots__=('ts','bp','bs','ap','asz','bid','ask')
    def __init__(self,ts,bp,bs,ap,asz):
        o=np.argsort(ts,kind='stable')
        self.ts=ts[o]; self.bp=bp[o]; self.bs=bs[o]; self.ap=ap[o]; self.asz=asz[o]
        self.bid=np.where(np.isfinite(self.bp[:,0]),self.bp[:,0],np.nan)
        self.ask=np.where(np.isfinite(self.ap[:,0]),self.ap[:,0],np.nan)
    def idx(self,t):
        return int(np.searchsorted(self.ts,t,side='right')-1)
    def lv(self,t):
        i=self.idx(t)
        if i<0: return None
        bp,bs,ap,asz=self.bp[i],self.bs[i],self.ap[i],self.asz[i]
        bids=[(float(p),float(q)) for p,q in zip(bp,bs) if p==p and q==q and q>0]
        asks=[(float(p),float(q)) for p,q in zip(ap,asz) if p==p and q==q and q>0]
        return bids,asks
    def mid(self,t):
        i=self.idx(t)
        if i<0: return np.nan
        b,a=self.bid[i],self.ask[i]
        return (b+a)/2 if (b==b and a==a) else np.nan

def load_all(flat_path,btc_bin):
    btc_start,closes=load_btc(btc_bin)
    d=pq.read_table(flat_path).to_pydict(); n=len(d['market_slug'])
    cols={k:np.asarray(d[k],dtype=np.float64) for k in d if k not in ('market_slug','outcome','winner')}
    slugs=d['market_slug']; outs=d['outcome']; winners=d['winner']
    endms=cols['end_ms'].astype(np.int64); stc=cols['seconds_to_close']
    B={}
    for side,keys in (('b',['b1','b2','b3','b4','b5','b6']),('bs',['bs1','bs2','bs3','bs4','bs5','bs6']),
                      ('a',['a1','a2','a3','a4','a5','a6']),('as',['as1','as2','as3','as4','as5','as6'])):
        B[side]=np.column_stack([cols[k] for k in keys]).astype(np.float32)
    # group contiguous runs
    markets=defaultdict(dict); run_start=0; cur=None; cur_o=None; end_by_slug={}; win_by_slug={}
    runs=[]
    for i in range(n):
        s=slugs[i]; o=outs[i]
        if s!=cur or o!=cur_o:
            if cur is not None: runs.append((cur,cur_o,run_start,i))
            cur=s; cur_o=o; run_start=i; end_by_slug[s]=int(endms[i])
        if o=='Up': win_by_slug[s]='Up' if winners[i]=='yes' else 'Down'
    runs.append((cur,cur_o,run_start,n))
    for s,o,a,b in runs:
        ts=endms[a:b]/1000.0-stc[a:b]
        side=Side(ts,B['b'][a:b],B['bs'][a:b],B['a'][a:b],B['as'][a:b])
        markets[s][o]=side
    out=[]
    for s,pair in markets.items():
        if 'Up' not in pair or 'Down' not in pair: continue
        end=end_by_slug[s]/1000.0; start=end-300.0
        i0=int(start)-btc_start
        target=float(np.mean(closes[max(0,i0-60):i0])) if i0>0 else float(closes[0])
        out.append({'slug':s,'start':start,'end':end,'target':target,'winner':win_by_slug[s],'Up':pair['Up'],'Down':pair['Down']})
    out.sort(key=lambda m:m['start'])
    return out,btc_start,closes
