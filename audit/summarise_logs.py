"""Audit tool: one line per run log (stage, metrics, green sequence). Usage: python audit/summarise_logs.py "results/run_logs/bellevue_116th_busy*"."""
import json,glob,sys,os
def stage(d):
    c=d['config']
    if d['controller_name']=='fixed': return 'S0'
    fw=c.get('forecast_weight',0) if c.get('use_forecast') else 0
    rw=c.get('spillback_risk_weight',0) if c.get('use_spillback_risk') else 0
    qw=c.get('queue_reach_weight',0) if c.get('use_queue_reach') else 0
    other=[k for k in('use_predictive','use_gap_out','use_discharge_green','use_pce_queue') if c.get(k)]+(['spill'] if c.get('spillback_weight') else [])+(['sw_margin'] if c.get('switching_margin') else [])+(['ratelim'] if c.get('green_rate_limit') else [])
    calib=any(a.get('axis_direction') for a in c['approaches'])
    return f"a={c['alpha']} F={fw} X={qw} S={rw} {'cal' if calib else 'geo'} {'+'.join(other)}"
pat=sys.argv[1]
for f in sorted(glob.glob(pat)):
    d=json.load(open(f))
    ag=d['evaluation_metrics']['aggregate']
    greens=[p for p in d['phases'] if p['state']=='GREEN']
    seq=' '.join(f"{p['approach'][0]}{int(p['green_time'])}{'*' if p['truncated'] else ''}" for p in greens)
    os_=sum(1 for p in greens if p.get('oversaturation',0)>0 and not p['truncated'])
    print(f"{os.path.basename(f)[-45:-5]:42s} {stage(d):32s} wait={ag['avg_waiting_time']:.2f} thr={ag['throughput']:.1f} served={ag['vehicles_served']} q={ag['avg_queue_length']:.3f} stops={sum(d.get('stops',{}).values())} os={os_}/{sum(1 for p in greens if not p['truncated'])} | {seq}")
