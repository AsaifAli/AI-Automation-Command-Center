from ui.output_formatting import build_client_report_markdown, parse_model_note


def test_content_report_is_client_ready_and_not_raw_json():
    report = build_client_report_markdown("content", {
        "posts": [{
            "topic": "AI agents in finance",
            "channel": "linkedin",
            "draft": "Finance teams can route reconciliation exceptions to accountable reviewers.",
        }],
        "strategy_note": "Lead with an operational use case.",
    }, {"run_id": "run-123", "status": "completed"})
    assert "Content Studio" in report
    assert "Draft 1 — Linkedin" in report
    assert "Finance teams can route" in report
    assert "human review" in report.lower()
    assert '"posts"' not in report


def test_competitor_report_decodes_json_analyst_note():
    note = '{"summary":"No matching source observations.","recommendation":"Add primary sources."}'
    report = build_client_report_markdown("competitor", {
        "competitors": [{"entity": "Acme", "priority": "unverified", "signals": [], "observed_items": [], "implication": "No matching items."}],
        "source_count": 1,
        "observed_item_count": 0,
        "analyst_note": note,
        "recommended_actions": ["Validate primary sources"],
    })
    assert "Competitor Intelligence Brief" in report
    assert "No matching source observations." in report
    assert "Validate primary sources" in report
    assert '"summary"' not in report


def test_outreach_report_keeps_human_approval_policy_visible():
    report = build_client_report_markdown("outreach", {
        "candidates": [{"name": "Acme", "channel": "linkedin", "message": "Hello Acme, would you be open to a conversation?", "approval_required": True}],
        "policy": "No autonomous external sending.",
    })
    assert "Partnership Outreach Brief" in report
    assert "Hello Acme" in report
    assert "Approval: Required" in report
    assert "No autonomous external sending" in report


def test_kpi_report_renders_updates_and_risks_as_prose():
    report = build_client_report_markdown("kpi", {
        "normalized_updates": [{"entity": "Portfolio A", "metric": "delivery", "value": "delayed", "status": "at_risk", "blocker": "Staffing"}],
        "risk_count": 1,
        "risks": [{"entity": "Portfolio A", "metric": "delivery", "blocker": "Staffing"}],
        "leadership_brief": "One update is at risk.",
        "executive_note": "Assign an owner to the staffing blocker.",
    })
    assert "Performance & Risk Briefing" in report
    assert "Staffing" in report
    assert "Assign an owner" in report


def test_model_note_parser_accepts_nested_structured_result():
    parsed = parse_model_note('{"summary":"A","actions":["B"]}')
    assert parsed["summary"] == "A"
    assert parsed["actions"] == ["B"]
