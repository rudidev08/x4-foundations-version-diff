#!/usr/bin/env python3
"""
Internal helper — verify or create `artifacts/<run>/settings.json`.

Invoked by run.sh once, before the pipeline starts. If settings.json already
exists it must match the resolved config (from .env + CLI); otherwise the
pipeline aborts with a specific mismatch message. On the first run of a
fresh artifact dir, the file is created.

Usage:
    python3 src/_ensure_settings.py --out DIR --v1 NAME --v2 NAME \\
        --model NAME --llm-cmd CMD --chunk-kb N [--force-split]

Not meant for direct user invocation. See run.sh for the canonical call.
"""
import argparse
from pathlib import Path

from _lib import check_or_write_settings


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--v1", required=True)
    p.add_argument("--v2", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--llm-cmd", required=True)
    p.add_argument("--chunk-kb", type=int, required=True)
    p.add_argument("--force-split", action="store_true")
    args = p.parse_args()

    check_or_write_settings(args.out, {
        "v1": args.v1,
        "v2": args.v2,
        "model_name": args.model,
        "llm_cmd": args.llm_cmd,
        "chunk_kb": args.chunk_kb,
        "force_split": args.force_split,
    })


if __name__ == "__main__":
    main()
