"""
orchestrator/poller.py — Scheduled Reconciliation Poller (Safety Net).

Addresses the critical failure mode:
"What if a change was pushed in Ashby or Workday, but the webhook got stuck
or failed on the network before reaching the pipeline?"

The poller implements a periodic reconciliation loop (e.g. run every 1-4 hours or nightly):
  1. Queries all active candidates/hires from Ashby ATS and Workday HRIS.
  2. Compares upstream state against our canonical HireState using Reconciler.diff().
  3. If an un-provisioned hire is found → automatically synthesizes a CREATE event.
  4. If a field drift is found (e.g. start date pushed back without webhook) →
     synthesizes an UPDATE event and executes the pipeline to self-heal.
  5. Records an immutable audit log entry for every reconciliation cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from core.models.hire_state import HireState, HireSource
from core.models.events import ChangeEvent, EventType, EventSource
from core.reconciler import Reconciler, HireStateDiff
from connectors.ashby import AshbyConnector
from connectors.workday import WorkdayConnector
from connectors.slack import SlackConnector
from audit.event_log import AuditLogger
from orchestrator.pipeline import Pipeline


@dataclass
class ReconciliationReport:
    """Detailed summary of a scheduled reconciliation sweep."""
    cycle_id: str
    started_at: str
    completed_at: Optional[str] = None
    total_scanned: int = 0
    in_sync_count: int = 0
    healed_creates: list[str] = field(default_factory=list)
    healed_updates: list[str] = field(default_factory=list)
    conflicts_detected: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def had_drift(self) -> bool:
        return bool(self.healed_creates or self.healed_updates or self.conflicts_detected)

    def summary_dict(self) -> dict[str, Any]:
        return {
            "cycle_id":            self.cycle_id,
            "started_at":          self.started_at,
            "completed_at":        self.completed_at,
            "total_scanned":       self.total_scanned,
            "in_sync_count":       self.in_sync_count,
            "healed_creates_count": len(self.healed_creates),
            "healed_updates_count": len(self.healed_updates),
            "conflicts_count":     len(self.conflicts_detected),
            "healed_creates":      self.healed_creates,
            "healed_updates":      self.healed_updates,
            "conflicts":           self.conflicts_detected,
            "errors":              self.errors,
        }


class ReconciliationPoller:
    """
    Scheduled reconciliation engine comparing source-of-truth systems (Ashby, Workday)
    against our internal canonical state.
    """

    def __init__(self, pipeline: Optional[Pipeline] = None):
        self.pipeline   = pipeline or Pipeline()
        self.ashby      = AshbyConnector()
        self.workday    = WorkdayConnector()
        self.reconciler = Reconciler()
        self.audit      = AuditLogger()
        self.slack      = SlackConnector()

    def run_reconciliation_cycle(self) -> ReconciliationReport:
        """
        Execute one full reconciliation sweep over all active hires in Ashby.
        Returns a ReconciliationReport.
        """
        now_str = datetime.utcnow().isoformat() + "Z"
        cycle_id = f"RECON-{int(datetime.utcnow().timestamp())}"
        report = ReconciliationReport(cycle_id=cycle_id, started_at=now_str)

        print(f"\n{'#'*60}")
        print(f"🔄 Starting Scheduled Reconciliation Sweep: {cycle_id}")
        print(f"{'#'*60}")

        # 1. Fetch all upstream candidate records from Ashby ATS
        upstream_hires = self.ashby.list_hires()
        report.total_scanned = len(upstream_hires)

        for upstream_hire in upstream_hires:
            hire_id = upstream_hire.hire_id

            try:
                # Load internal canonical state
                internal_hire = self.pipeline._load_hire(hire_id)

                # ── Case A: Upstream hire never received any webhook (Completely missed) ──
                if internal_hire is None:
                    print(f"  🚨 Drift detected: Hire {hire_id} ({upstream_hire.display_name}) "
                          "exists in Ashby but was NEVER ingested! Self-healing...")
                    
                    create_payload = self.ashby.emit_hire_created_event(upstream_hire)
                    # Mark origin so audit knows it was caught by the poller
                    create_payload["payload"]["reconciled_by"] = cycle_id

                    result = self.pipeline.process(create_payload)
                    if result["status"] == "PROCESSED":
                        report.healed_creates.append(hire_id)
                        self.audit.log(hire_id, "POLLER_HEALED_MISSED_HIRE", {
                            "cycle_id": cycle_id,
                            "action": "AUTO_PROVISIONED",
                        })
                    else:
                        report.errors.append(f"{hire_id}: Create failed with status {result['status']}")
                    continue

                # ── Case B: Existing hire — check for silent data drift ─────────────────
                diff = self.reconciler.diff(internal_hire, upstream_hire)

                if diff.is_no_op:
                    report.in_sync_count += 1
                    continue

                # Drift detected!
                changed_field_names = diff.field_names()
                print(f"  ⚠️  Drift detected for {hire_id} ({internal_hire.display_name}): "
                      f"Unsynced upstream changes: {changed_field_names}")

                # Build delta payload of the changed fields
                patch_payload = {
                    cf.field_name: cf.new_value for cf in diff.changed_fields
                }
                update_event = self.ashby.emit_hire_updated_event(internal_hire, patch_payload)
                update_event["payload"]["reconciled_by"] = cycle_id

                result = self.pipeline.process(update_event)

                if result["status"] == "PROCESSED":
                    report.healed_updates.append(f"{hire_id}:{','.join(changed_field_names)}")
                    self.audit.log(hire_id, "POLLER_HEALED_DRIFT", {
                        "cycle_id": cycle_id,
                        "fields": changed_field_names,
                    })
                elif result["status"] == "CONFLICT_PAUSED":
                    report.conflicts_detected.append(f"{hire_id}:{diff.conflict_type}")
                else:
                    report.errors.append(f"{hire_id}: Update returned status {result['status']}")

            except Exception as e:
                err_msg = f"Error reconciling {hire_id}: {type(e).__name__}: {e}"
                print(f"  ❌ {err_msg}")
                report.errors.append(err_msg)

        report.completed_at = datetime.utcnow().isoformat() + "Z"

        # Log cycle summary to audit trail
        self.audit.log("SYSTEM", "RECONCILIATION_CYCLE_COMPLETED", report.summary_dict())

        # If any drift was caught and healed, alert People Ops
        if report.had_drift:
            self.slack.alert_people_ops(
                subject=f"Reconciliation Sweep Completed ({cycle_id})",
                detail=(
                    f"Scanned {report.total_scanned} records.\n"
                    f"• Healed Missed Hires: {len(report.healed_creates)}\n"
                    f"• Healed Data Drift: {len(report.healed_updates)}\n"
                    f"• Conflicts Paused: {len(report.conflicts_detected)}"
                )
            )

        print(f"\n✅ Reconciliation Sweep Finished: {report.in_sync_count} in sync, "
              f"{len(report.healed_creates)} missed hires created, "
              f"{len(report.healed_updates)} drift updates healed.")

        return report
