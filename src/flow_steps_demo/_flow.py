"""Default FlowSpec inherited by all specs defined in this directory or child folders.

Also serves as the inspect_ai entry point for task registration.
"""

from inspect_ai.model import GenerateConfig

from flow_steps_demo.alignment_probe.task import alignment_probe  # noqa: F401

from inspect_flow import (
    FlowSpec,
    FlowDefaults,
    FlowOptions,
    FlowStoreConfig,
)

from flow_steps_demo.constants import (
    STORE_PATH,
    LOG_DIR_DEV,
    TAG_QA_AUTO_NEEDED,
)


FlowSpec(
    # Default log directory is the dev dir
    log_dir=LOG_DIR_DEV,
    # Create a new uniquely-named log dir (timestamp) for each run
    log_dir_create_unique=True,
    # Default store config: write-only, no log matching by default
    store=FlowStoreConfig(
        path=STORE_PATH,
    ),
    # Tag all logs with TAG_QA_AUTO_NEEDED by default
    options=FlowOptions(
        tags=[TAG_QA_AUTO_NEEDED],
    ),
    # Default generate config applied to all models unless overridden
    defaults=FlowDefaults(
        config=GenerateConfig(
            max_connections=300,
            max_tokens=100,
        ),
    ),
)
