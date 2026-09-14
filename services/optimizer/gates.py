"""Statistical acceptance gate for candidate-vs-baseline quality samples."""
from dataclasses import dataclass
from math import erf,sqrt,isfinite
from statistics import mean,pstdev
from typing import Sequence
@dataclass(frozen=True)
class GateResult:
    accepted: bool; mean_delta: float; p_value: float; reason: str; confidence_interval: tuple[float,float]=(-1.0,1.0)
@dataclass(frozen=True)
class StatisticalGate:
    min_quality_delta: float=0.0; alpha: float=0.05; min_samples_for_significance: int=5; max_quality_regression: float=0.0
    def __post_init__(self):
        if (isinstance(self.min_samples_for_significance, bool) or not isinstance(self.min_samples_for_significance, int) or self.min_samples_for_significance < 2 or not 0<self.alpha<1 or not isfinite(self.alpha) or not 0<=self.min_quality_delta<=1 or not 0<=self.max_quality_regression<=1): raise ValueError("invalid statistical gate settings")
    def test(self,baseline:Sequence[float],candidate:Sequence[float])->GateResult:
        baseline=tuple(baseline); candidate=tuple(candidate)
        if len(baseline)!=len(candidate) or not baseline: raise ValueError("baseline and candidate must contain paired, non-empty samples")
        for sample in baseline+candidate:
            if isinstance(sample,bool) or not isfinite(float(sample)) or not 0<=float(sample)<=1: raise ValueError("quality samples must be finite and in [0, 1]")
        deltas=tuple(float(c)-float(b) for b,c in zip(baseline,candidate)); delta=mean(deltas); sd=pstdev(deltas)
        if len(deltas)<self.min_samples_for_significance: return GateResult(False,delta,1.0,"insufficient samples",(delta,delta))
        if sd==0:
            p=0.0 if delta>0 else 1.0; interval=(delta,delta)
        else:
            se=sd/sqrt(len(deltas)); z=delta/se; p=0.5*(1-erf(z/sqrt(2))); margin=1.96*se; interval=(delta-margin,delta+margin)
        threshold=self.min_quality_delta if self.min_quality_delta>0 else -self.max_quality_regression
        significance=self.min_quality_delta<=0 or p<=self.alpha
        accepted=delta>=threshold and interval[0]>=-self.max_quality_regression and significance
        return GateResult(accepted,delta,p,"quality change is within tolerance" if accepted else "quality gate not met",interval)
    def accept(self,baseline:Sequence[float],candidate:Sequence[float])->bool: return self.test(baseline,candidate).accepted
