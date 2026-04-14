"""FlowSpec for the alignment evaluation sweep.

Generates the full cross-product of models x training protocols x misalignment
types x themes. Optionally filtered to a single model.

Usage:
    # Check completeness against existing logs
    flow check src/flow_steps_demo/spec.py \
        --arg model=openai/gpt-4o

    # Preview the resolved YAML config
    flow config src/flow_steps_demo/spec.py \
        --arg model=openai/gpt-4o

    # Dry-run all tasks
    flow run src/flow_steps_demo/spec.py --dry-run
"""

from inspect_flow import FlowSpec, FlowStoreConfig, tasks_matrix
from flow_steps_demo.filters import qa_done
from flow_steps_demo.constants import (
    MODELS,
    LOG_DIR_DEV,
    STORE_PATH,
    MISALIGNMENT_TYPES,
    TRAINING_PROTOCOLS,
    THEMES,
)


def dev(model: str = ""):
    if not model:
        models = sum(MODELS.values(), [])
    else:
        models = [model]

    return FlowSpec(
        log_dir=f"{LOG_DIR_DEV}/{model}" if model else LOG_DIR_DEV,
        store=FlowStoreConfig(
            path=STORE_PATH,
            read=True,
            filter=qa_done,
        ),
        tasks=tasks_matrix(
            task="flow_steps_demo/alignment_probe",
            model=models,
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
