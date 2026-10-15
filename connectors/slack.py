"""
connectors/slack.py — Mock Slack notifications client.

Simulates Slack DMs and channel messages for onboarding events.
In production, replace with Slack Web API (slack_sdk).
"""

from __future__ import annotations

from datetime import datetime

from connectors.base import BaseConnector
from core.models.hire_state import HireState


# Channel constants (match real workspace channel names)
CHANNEL_PEOPLE_OPS_ALERTS = "#people-ops-alerts"
CHANNEL_IT_ALERTS         = "#it-alerts"
CHANNEL_DAY1_COHORT       = "#day1-cohort-oct"      # Updated monthly in production


class SlackConnector(BaseConnector):
    """Mock Slack connector for DMs and channel messages."""

    SECTION = "slack_messages"

    def _record_message(self, recipient: str, message: str, message_type: str) -> str:
        """Append a message to the mock store and return a fake message ID."""
        messages = self._get_section(self.SECTION)
        if not isinstance(messages, list):
            messages = []

        msg_id = f"slack_{datetime.utcnow().timestamp():.0f}"
        messages.append({
            "msg_id":       msg_id,
            "recipient":    recipient,
            "message":      message,
            "message_type": message_type,
            "sent_at":      datetime.utcnow().isoformat(),
        })
        self._set_section(self.SECTION, messages)
        return msg_id

    def send_dm(self, to_email: str, message: str) -> str:
        """Send a direct message to a user (identified by email)."""
        msg_id = self._record_message(to_email, message, "DM")
        print(f"  [Slack] 💬 DM → {to_email}: {message[:80]}{'...' if len(message) > 80 else ''}")
        return msg_id

    def post_channel_message(self, channel: str, message: str) -> str:
        """Post a message to a Slack channel."""
        msg_id = self._record_message(channel, message, "CHANNEL")
        print(f"  [Slack] 📢 {channel}: {message[:80]}{'...' if len(message) > 80 else ''}")
        return msg_id

    def alert_people_ops(self, subject: str, detail: str) -> str:
        """Post a structured alert to the People Ops alerts channel."""
        message = f"🚨 *{subject}*\n{detail}"
        return self.post_channel_message(CHANNEL_PEOPLE_OPS_ALERTS, message)

    def alert_it(self, subject: str, detail: str) -> str:
        """Post a structured alert to the IT alerts channel."""
        message = f"🔧 *{subject}*\n{detail}"
        return self.post_channel_message(CHANNEL_IT_ALERTS, message)

    def notify_hire_day1_ready(self, hire: HireState) -> str:
        """
        Post a Day-1 ready notification to the cohort channel.
        Sent when all stage gates are complete.
        """
        message = (
            f"✅ *Day-1 Ready: {hire.display_name}*\n"
            f"> Title: {hire.job_title} | Dept: {hire.department} | Team: {hire.team}\n"
            f"> Start Date: {hire.start_date} | Manager: {hire.manager_email}\n"
            f"> Laptop: {hire.laptop_model} → {hire.shipping_address.city}, {hire.shipping_address.state}"
        )
        return self.post_channel_message(CHANNEL_DAY1_COHORT, message)

    def notify_conflict(self, hire: HireState, conflict_type: str, detail: str) -> str:
        """Alert People Ops about a conflict requiring review."""
        subject = f"Onboarding Conflict — {hire.display_name} [{conflict_type}]"
        return self.alert_people_ops(subject, detail)

    def notify_dead_letter(self, hire_id: str, error: str) -> str:
        """Alert People Ops when a hire event fails and hits the dead letter queue."""
        subject = f"Dead Letter: hire_id={hire_id}"
        return self.alert_people_ops(subject, f"Error: {error}")

    def add_to_cohort(self, hire: HireState) -> str:
        """Invite hire to the Day-1 cohort Slack channel."""
        message = (
            f"👋 Welcome to your cohort, {hire.display_name}! "
            f"Starting {hire.start_date} as {hire.job_title}."
        )
        return self.post_channel_message(CHANNEL_DAY1_COHORT, message)

    def get_messages(self) -> list[dict]:
        """Return all recorded Slack messages (for test assertions)."""
        messages = self._get_section(self.SECTION)
        return messages if isinstance(messages, list) else []
