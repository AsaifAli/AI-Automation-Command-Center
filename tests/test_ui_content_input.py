from ui.content_input import parse_topic_briefs


def test_content_briefs_split_on_lines_and_preserve_commas():
    brief = (
        "AI agents in enterprise finance operations, including reconciliations\n"
        "Multi-agent controls, audit trails, and human approvals\n"
        "   \n"
    )

    assert parse_topic_briefs(brief) == [
        "AI agents in enterprise finance operations, including reconciliations",
        "Multi-agent controls, audit trails, and human approvals",
    ]
