#!/usr/bin/env python3
import argparse, json
from aws_local import load_span_count
parser=argparse.ArgumentParser(); parser.add_argument('--spans', type=int, default=100000); args=parser.parse_args(); print(json.dumps(load_span_count(args.spans), sort_keys=True))
