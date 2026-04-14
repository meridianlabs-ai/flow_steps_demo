import os

bucket = os.environ.get("FLOW_DEMO_BUCKET", "./output")
BUCKET = os.path.abspath(bucket) if not bucket.startswith("s3://") else bucket

STORE_PATH = f"{BUCKET}/store"

LOG_DIR_DEV = f"{BUCKET}/dev/logs"
LOG_DIR_PROD = f"{BUCKET}/prod/logs"

SCAN_DIR = f"{BUCKET}/scans"

TAG_QA_AUTO_NEEDED = "qa_auto_needed"
TAG_QA_AUTO_DONE = "qa_auto_done"
TAG_QA_MANUAL_NEEDED = "qa_manual_needed"
TAG_QA_MANUAL_DONE = "qa_manual_done"
TAG_PROMOTED = "promoted"

MODELS = {
    "openai": [
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
    ],
    "anthropic": [
        "anthropic/claude-sonnet-4-20250514",
        "anthropic/claude-haiku-4-5-20251001",
    ],
    "grok": [
        "grok/grok-3",
        "grok/grok-3-mini",
    ],
}

TRAINING_PROTOCOLS = [
    "sft_baseline",
    "dpo_iterative",
    "cai_constitutional",
]

MISALIGNMENT_TYPES = [
    "sycophancy",
    "deceptive_alignment",
    "reward_hacking",
]

THEMES = [
    "baseline",
    "self_preservation",
    "oversight_subversion",
]
