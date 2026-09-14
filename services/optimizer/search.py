"""Node substitution and bounded beam search for model assignments."""
from dataclasses import dataclass,replace
from typing import Any,Callable,Iterable
import math
from numbers import Real

def _metric(v:Any,n:str,lo:float|None=None,hi:float|None=None)->float:
    if isinstance(v,bool) or not isinstance(v,Real) or not math.isfinite(float(v)): raise ValueError(f"{n} must be finite")
    v=float(v)
    if lo is not None and v<lo or hi is not None and v>hi: raise ValueError(f"{n} is out of range")
    return v
@dataclass(frozen=True)
class AssignmentCandidate:
    config: dict[str,str]
    quality: float
    cost_usd: float
    latency_ms: float
    baseline_quality: float|None = None
    def __post_init__(self)->None:
        _metric(self.quality,"quality",0,1); _metric(self.cost_usd,"cost_usd",0); _metric(self.latency_ms,"latency_ms",0)
        if not isinstance(self.config, dict): raise ValueError("config must be a mapping")
        if self.baseline_quality is not None: _metric(self.baseline_quality,"baseline_quality",0,1)
    @property
    def quality_delta(self)->float: return self.quality if self.baseline_quality is None else self.quality-self.baseline_quality

def search_assignments(baseline:dict[str,str],models:Iterable[str],evaluate:Callable[[dict[str,str]],AssignmentCandidate],*,beam_width:int=8,max_quality_regression:float=0.01,max_latency_ms:float|None=None,max_cost_usd:float|None=None,max_spend_usd:float|None=None)->tuple[AssignmentCandidate,...]:
    if not baseline or isinstance(beam_width, bool) or not isinstance(beam_width, int) or beam_width<1: raise ValueError("baseline and beam_width are invalid")
    _metric(max_quality_regression,"quality tolerance",0,1)
    if max_cost_usd is not None and max_spend_usd is not None and max_cost_usd!=max_spend_usd: raise ValueError("cost and spend limits disagree")
    spend=max_cost_usd if max_spend_usd is None else max_spend_usd
    if max_latency_ms is not None: _metric(max_latency_ms,"latency limit",0)
    if spend is not None: _metric(spend,"spend limit",0)
    options=tuple(dict.fromkeys(models))
    if not options: raise ValueError("models must not be empty")
    base=evaluate(dict(baseline)); baseq=base.quality; threshold=baseq-max_quality_regression
    def eligible(c): return c.quality>=threshold and (max_latency_ms is None or c.latency_ms<=max_latency_ms) and (spend is None or c.cost_usd<=spend)
    if not eligible(base): return ()
    base=replace(base,baseline_quality=baseq); beam=[base]
    for node in tuple(baseline):
        pool=list(beam); seen={tuple(sorted(c.config.items())) for c in pool}
        for prior in beam:
            for model in options:
                if model==prior.config.get(node): continue
                config=dict(prior.config); config[node]=model; key=tuple(sorted(config.items()))
                if key in seen: continue
                seen.add(key); candidate=evaluate(config)
                if eligible(candidate): pool.append(replace(candidate,baseline_quality=baseq))
        beam=sorted(pool,key=lambda c:(-c.quality,c.cost_usd,c.latency_ms,tuple(sorted(c.config.items()))))[:beam_width]
    return tuple(beam)
