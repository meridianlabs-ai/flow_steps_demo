"""Default FlowSpec for the project.

Imports filters and steps so they are registered and discoverable by Flow.
The FlowSpec defined here provides default settings (store, tags, generation
config) and is automatically included by other specs in this repo, which can
override any of these defaults.
"""

from inspect_ai.model import GenerateConfig

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
from flow_steps_demo.filters import qa_done, scan_has_refusal  # noqa: F401 — registered for discovery
from flow_steps_demo.steps import qa_auto, manual_review_done, promote  # noqa: F401 — registered for discovery


FlowSpec(
    log_dir=LOG_DIR_DEV,
    log_dir_create_unique=True,
    store=FlowStoreConfig(
        path=STORE_PATH,
    ),
    options=FlowOptions(
        tags=[TAG_QA_AUTO_NEEDED],
    ),
    defaults=FlowDefaults(
        config=GenerateConfig(
            max_connections=300,
            max_tokens=100,
        ),
    ),
)
