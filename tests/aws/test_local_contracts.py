from scripts.aws_local import SimulatedJob, delete_artifacts, load_span_count, process_candidate, readiness

def test_duplicate_candidate_is_not_charged_twice():
 j=SimulatedJob("j"); ok,spent=process_candidate(j,"c",1,0,2); again,spent2=process_candidate(j,"c",1,spent,2); assert ok and not again and spent2==1

def test_spend_cap_stops_execution():
 try: process_candidate(SimulatedJob("j"),"c",2,0,1)
 except RuntimeError as e: assert "spend cap" in str(e)
 else: raise AssertionError("expected cap")

def test_s3_delete_removes_requested_objects(): assert delete_artifacts(["a"], {"a","b"})=={"b"}
def test_readiness_tracks_database(): assert readiness(True) and not readiness(False)
def test_span_load_is_local_only(): assert load_span_count(100000)=={"accepted":100000,"aws_calls":0,"corruption":False}
