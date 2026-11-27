from urllib.parse import urlparse

from ui.legacy_notice import legacy_workspace_url


def test_legacy_ui_points_to_the_authenticated_studio_workspace():
    destination = urlparse(legacy_workspace_url())

    assert destination.scheme == "https"
    assert destination.netloc == "studio.asaifali-studio.workers.dev"
    assert destination.path == "/workspace"
