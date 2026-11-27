"""Static destination for the retired standalone FlowPilot UI."""

STUDIO_WORKSPACE_URL = "https://studio.asaifali-studio.workers.dev/workspace"


def legacy_workspace_url() -> str:
    """Return the same-origin Studio workspace entry point."""
    return STUDIO_WORKSPACE_URL
