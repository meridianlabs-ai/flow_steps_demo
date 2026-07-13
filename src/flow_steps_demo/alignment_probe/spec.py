"""FlowSpec for the alignment evaluation sweep.

Generates the full cross-product of models x training protocols x misalignment
types x themes. Optionally filtered to a single model.

Usage:
    # Check completeness against existing logs
    flow check src/flow_steps_demo/alignment_probe/spec.py \
        --log-dir ./output/dev/logs
        --arg model=openai/gpt-4o

    # Preview the resolved YAML config
    flow config src/flow_steps_demo/alignment_probe/spec.py \
        --arg model=openai/gpt-4o

    # Dry-run all tasks
    flow run src/flow_steps_demo/alignment_probe/spec.py --dry-run
"""

from inspect_flow import FlowSpec, FlowStoreConfig, tasks_matrix
from flow_steps_demo.filters import qa_done
from flow_steps_demo.constants import LOG_DIR_DEV, STORE_PATH


MODELS = [
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "anthropic/claude-sonnet-4-20250514",
    "anthropic/claude-haiku-4-5-20251001",
    "grok/grok-3",
    "grok/grok-3-mini",
]

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


def spec(model: str = ""):
    return FlowSpec(
        log_dir=f"{LOG_DIR_DEV}/{model}" if model else LOG_DIR_DEV,
        # By default Flow writes to the store but doesn't read from it. Setting
        # read=True enables both reading and writing. The filter ensures only log
        # files tagged as having passed QA are matched from the store.
        store=FlowStoreConfig(
            path=STORE_PATH,
            read=True,
            filter=qa_done,
        ),
        tasks=tasks_matrix(
            task="flow_steps_demo/alignment_probe",
            model=MODELS if not model else [model],
            args=[
                {
                    "training_protocol": tp,
                    "misalignment_type": mt,
                    "theme": th,
                }
                for tp in TRAINING_PROTOCOLS
                for mt in MISALIGNMENT_TYPES
                for th in THEMES
            ],
        ),
    )
