"""
agent/prompts.py — Structured prompt templates for Claude conflict resolution.

Each function returns a complete prompt string for a specific conflict scenario.
Prompts are designed to elicit a structured JSON response from Claude.
"""

from __future__ import annotations

from core.models.hire_state import HireState
from core.reconciler import HireStateDiff


def build_conflict_resolution_prompt(
    hire: HireState,
    diff: HireStateDiff,
    audit_log: list[dict],
) -> str:
    """
    Build the conflict resolution prompt for Claude.
    Returns a prompt that asks Claude to analyze the conflict and return
    a structured JSON action plan.
    """

    audit_summary = "\n".join([
        f"  [{e['timestamp']}] {e['action']}: {e['detail']}"
        for e in audit_log[-10:]  # Last 10 events for context
    ]) or "  (No prior actions recorded)"

    changed_summary = "\n".join([
        f"  - {cf.field_name}: {cf.old_value!r} → {cf.new_value!r} [risk={cf.risk_level.value}]"
        for cf in diff.changed_fields
    ])

    return f"""You are an intelligent onboarding operations assistant at BetterUp.
A change event has been received for a new hire that cannot be automatically resolved
by standard rules. Your job is to analyze the situation and provide a clear, structured action plan.

## Hire Information
- Name: {hire.display_name} (Legal: {hire.full_legal_name})
- Hire ID: {hire.hire_id}
- Start Date: {hire.start_date}
- Job Title: {hire.job_title} | Dept: {hire.department} | Team: {hire.team}
- Manager: {hire.manager_email}
- Equipment Status: {hire.equipment_status.value}
- Okta Status: {hire.okta_status.value}

## Current Shipping Address
{hire.shipping_address.one_line()}

## Conflict Detected
- Conflict Type: {diff.conflict_type}
- Conflict Detail: {diff.conflict_detail}

## Changed Fields
{changed_summary}

## Recent Audit History
{audit_summary}

## Your Task
Analyze this conflict and respond with a JSON object (no markdown, no explanation outside the JSON):

{{
  "conflict_summary": "One sentence summary of what the conflict is",
  "risk_assessment": "LOW | MEDIUM | HIGH | CRITICAL",
  "recommended_actions": [
    {{
      "action": "CANCEL_EQUIPMENT_ORDER | UPDATE_SHIPPING_ADDRESS | REORDER_EQUIPMENT | UPDATE_OKTA | UPDATE_WORKDAY | NOTIFY_PEOPLE_OPS | NOTIFY_IT | NOTIFY_MANAGER | PAUSE_PROVISIONING | NO_ACTION",
      "reason": "Why this action is needed",
      "connector": "equipment | okta | workday | slack | lumos",
      "params": {{}}
    }}
  ],
  "escalate_to_human": true | false,
  "escalation_message": "If escalate_to_human is true, the exact Slack message to send to People Ops",
  "rationale": "Full explanation of your reasoning"
}}

Rules:
- If equipment is SHIPPED and address changed, determine if cancellation or address update is more appropriate based on urgency.
- If start date is within 72 hours, always recommend NOTIFY_PEOPLE_OPS and NOTIFY_IT.
- If legal name changed, always escalate_to_human = true.
- If multiple high-risk changes, recommend PAUSE_PROVISIONING until human review.
- Be specific in params (e.g., include hire_id, new values, etc.).
- Respond ONLY with valid JSON. No markdown code blocks.
"""
