"""Offline replay: drive the real AdaptiveController/PhaseSequencer from logged
(controller-invariant) per-frame metrics, under arbitrary score weights."""
import json, sys, dataclasses
sys.path.insert(0, '.')
from src.config import load_config
from src.signal_controller import AdaptiveController, PhaseSequencer
from src.traffic_metrics import ApproachMetrics, compute_config_scores
A=['North','East','South','West']
FIELDS=['vehicle_count','vehicle_density','queue_length','normalized_queue','normalized_arrival','queue_pce','normalized_spillback','normalized_forecast','queue_reach','spillback_risk']
def replay(log, cfg):
    fr=log['frames']
    dep={a:[0]*len(fr) for a in A}
    for i in range(1,len(fr)):
        for a in A:
            d=fr[i-1]['approaches'][a]['queue_length']-fr[i]['approaches'][a]['queue_length']
            dep[a][i]=max(d,0)
    seq=PhaseSequencer(AdaptiveController(cfg),cfg,30.0)
    scores={a:0.0 for a in A}; served=0; wait=0.0
    for i,f in enumerate(fr):
        seq.tick(i,scores); st=seq.signal_states()
        m={a:ApproachMetrics(approach=a,**{k:f['approaches'][a][k] for k in FIELDS}) for a in A}
        scores=compute_config_scores(m,cfg)
        for a in A:
            if st[a].value=='GREEN': served+=dep[a][i]
            else: wait+=f['approaches'][a]['queue_length']/30.0
    seq.finalize(len(fr)-1)
    g=' '.join(f"{p.approach[0]}{int(p.green_time)}{'*' if p.truncated else ''}" for p in seq.phases if p.state.value=='GREEN')
    return served, wait, g
base=load_config('config/ablation_s4_proposed_calibrated.json')
V=lambda **kw: dataclasses.replace(base, **kw)
variants={
 'S2 a.5 (D+Q)':V(use_forecast=False,forecast_weight=0,use_spillback_risk=False,spillback_risk_weight=0,use_queue_reach=False),
 'S3 .7B+.3F':V(use_spillback_risk=False,spillback_risk_weight=0,use_queue_reach=False),
 'S4 .4B+.3F+.3S':base,
 'NULL .4B+.3F+.3*0 (S zeroed)':'null',
 'S4-X .4B+.3F+.3X (reach, no projection)':V(use_spillback_risk=False,spillback_risk_weight=0,use_queue_reach=True,queue_reach_weight=0.3),
 'S3 F=.4':V(use_spillback_risk=False,spillback_risk_weight=0,use_queue_reach=False,forecast_weight=0.4),
 'S1 a=1':V(alpha=1.0,use_forecast=False,forecast_weight=0,use_spillback_risk=False,spillback_risk_weight=0,use_queue_reach=False),
}
for clip,run in [('busy','bellevue_116th_busy__adaptive__alpha0p50__20260913-232912'),('dev','bellevue_116th_dev__adaptive__alpha0p50__20260914-090322'),('final','bellevue_116th_final__adaptive__alpha0p50__20260914-095045')]:
    log=json.load(open(f'results/run_logs_legacy/{run}.json'))
    print('==',clip)
    for name,cfg in variants.items():
        if cfg=='null':
            l2=json.loads(json.dumps(log))
            for f in l2['frames']:
                for a in A: f['approaches'][a]['spillback_risk']=0.0
            s,w,g=replay(l2,base)
        else: s,w,g=replay(log,cfg)
        print(f"  {name:42s} served={s:4d} queued-veh-sec-on-red={w:7.1f} | {g}")
