"""Pareto frontier and deterministic recommendation selection."""
from collections.abc import Iterable
import math
from .staged import Candidate
def _dominates(left:Candidate,right:Candidate)->bool:
    nw=left.cost_usd<=right.cost_usd and left.latency_ms<=right.latency_ms and left.quality>=right.quality
    return nw and (left.cost_usd<right.cost_usd or left.latency_ms<right.latency_ms or left.quality>right.quality)
def _filtered(candidates, max_latency_ms=None,max_cost_usd=None,max_spend_usd=None):
    if max_cost_usd is not None and max_spend_usd is not None and max_cost_usd!=max_spend_usd: raise ValueError("cost and spend limits disagree")
    spend=max_cost_usd if max_spend_usd is None else max_spend_usd
    for value,name in ((max_latency_ms,"latency limit"),(spend,"spend limit")):
        if value is not None and (isinstance(value,bool) or not math.isfinite(float(value)) or float(value)<0): raise ValueError(f"{name} is invalid")
    pool=list(candidates)
    if len({c.id for c in pool})!=len(pool): raise ValueError("candidate ids must be unique")
    return [c for c in pool if (max_latency_ms is None or c.latency_ms<=max_latency_ms) and (spend is None or c.cost_usd<=spend)]
def pareto_frontier(candidates:Iterable[Candidate],*,max_latency_ms:float|None=None,max_cost_usd:float|None=None,max_spend_usd:float|None=None)->list[Candidate]:
    pool=_filtered(candidates,max_latency_ms,max_cost_usd,max_spend_usd); frontier=[c for c in pool if not any(_dominates(o,c) for o in pool if o.id!=c.id)]
    return sorted(frontier,key=lambda c:c.id)
def recommend(candidates:Iterable[Candidate],quality_weight:float=1.0,cost_weight:float=0.1,latency_weight:float=0.001,*,max_latency_ms:float|None=None,max_cost_usd:float|None=None,max_spend_usd:float|None=None)->Candidate:
    if any(isinstance(x,bool) or not math.isfinite(float(x)) or float(x)<0 for x in (quality_weight,cost_weight,latency_weight)): raise ValueError("recommendation weights must be finite and non-negative")
    pool=_filtered(candidates,max_latency_ms,max_cost_usd,max_spend_usd)
    if not pool: raise ValueError("at least one candidate is required")
    return min(pool,key=lambda c:(-(quality_weight*c.quality-cost_weight*c.cost_usd-latency_weight*c.latency_ms),-c.quality,c.cost_usd,c.latency_ms,c.id))
