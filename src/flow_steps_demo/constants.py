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
