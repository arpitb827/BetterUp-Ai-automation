# 🗺️ BetterUp Onboarding Architecture & Systems Flow Map
## Slice A: Change Propagation & Event Reconciliation Engine

This document maps out how data moves across systems, where the state transitions happen, and how deterministic logic pairs with AI exception handling.

---

### 1. End-to-End System Architecture

```mermaid
flowchart TD
    subgraph Sources["📥 Ingestion Sources"]
        Ashby["<b>Ashby ATS</b><br/>Offer Accepted / Candidate Edits"]
        WorkdaySrc["<b>Workday HRIS</b><br/>Off-cycle Hire / Direct Edits"]
        Poller["<b>Reconciliation Poller</b><br/>orchestrator/poller.py<br/><i>Periodic Sweep / Self-Healing</i>"]
    end

    subgraph CoreEngine["⚙️ Reconciliation Engine (Core Logic)"]
        Ingest["<b>Pipeline Ingestion</b><br/>orchestrator/pipeline.py"]
        LockMgr["<b>Per-Hire Lock Manager & OCC</b><br/><i>Serializes concurrent webhooks</i>"]
        IdemCheck{"<b>Idempotency Check</b><br/>core/idempotency.py<br/><i>SHA256(hire_id + payload)</i>"}
        SchemaVal["<b>Schema Validation</b><br/>core/models/hire_state.py<br/><i>Pydantic v2 Canonical Contract</i>"]
        Reconciler["<b>Diff & Risk Evaluator</b><br/>core/reconciler.py<br/><i>Compares Old vs New State</i>"]
        StateMachine["<b>State Machine & Gates</b><br/>core/state_machine.py<br/><i>DISCOVERED → VALIDATING → ...</i>"]
    end

    subgraph Branching{"Decision Point"}
        DiffCheck{"Conflict or<br/>High-Risk Anomaly?"}
    end

    subgraph StandardPath["🟢 Deterministic Dispatch Path"]
        Dispatcher["<b>Dispatcher Engine</b><br/>orchestrator/dispatcher.py"]
        WD["<b>Workday</b><br/>Create/Update Employee"]
        OK["<b>Okta</b><br/>Provision/Update User"]
        LU["<b>Lumos</b><br/>Assign App Bundles"]
        EQ["<b>Equipment Logistics</b><br/>Order/Update Hardware"]
        SL["<b>Slack Cohort</b><br/>Add to #day1-cohort"]
    end

    subgraph AIPath["🟣 Agentic Exception Path (LLM Resolver)"]
        Gemini["<b>AI Exception Resolver</b><br/>agent/claude_resolver.py<br/><i>(Gemini / Claude via API)</i>"]
        DecisionAction{"Escalate to<br/>Human?"}
        PeopleOpsAlert["<b>People Ops Alert</b><br/>#people-ops-alerts (Slack)"]
        AutoAction["<b>Automated Intervention</b><br/>Cancel shipment / Re-order"]
    end

    subgraph Persistence["💾 Audit & Storage"]
        AuditLog[("<b>Immutable Audit Log</b><br/>audit/event_log.jsonl")]
        IdemDB[("<b>Idempotency Store</b><br/>SQLite: data/idempotency.db")]
        MockDB[("<b>Systems State Store</b><br/>data/mock_db.json")]
    end

    %% Flow connections
    Ashby -->|Webhook Event| Ingest
    WorkdaySrc -->|Webhook Event| Ingest
    Ingest --> IdemCheck
    
    IdemCheck -->|Duplicate Key| SkipLog["Discard / No-op"]
    IdemCheck -->|New Event| SchemaVal
    
    SchemaVal --> StateMachine
    StateMachine --> Reconciler
    Reconciler --> DiffCheck

    DiffCheck -->|No Conflict / Standard Change| Dispatcher
    DiffCheck -->|Conflict Detected<br/>e.g. Address changed post-ship| Gemini

    Dispatcher --> WD
    Dispatcher --> OK
    Dispatcher --> LU
    Dispatcher --> EQ
    Dispatcher --> SL

    Gemini --> DecisionAction
    DecisionAction -->|Yes: Legal mismatch / high risk| PeopleOpsAlert
    DecisionAction -->|No: Actionable rule| AutoAction
    AutoAction --> EQ

    %% Storage hooks
    Dispatcher -.-> AuditLog
    Gemini -.-> AuditLog
    Ingest -.-> IdemDB
    WD & OK & LU & EQ & SL -.-> MockDB
```

---

### 2. Event Lifecycle Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    actor Webhook as Ashby / Workday
    participant Pipeline as Pipeline Ingestion
    participant Idem as Idempotency Store
    participant Rec as Reconciler (Diff)
    participant SM as State Machine
    participant AI as Gemini/Claude Arbiter
    participant Disp as Dispatcher
    participant External as Downstream (Okta/Eq/Slack)
    participant Audit as Audit Log (JSONL)

    Webhook->>Pipeline: POST /events (ChangeEvent)
    Pipeline->>Idem: has_seen(SHA256)?
    alt Event Already Processed (Replay)
        Idem-->>Pipeline: true
        Pipeline-->>Webhook: 200 OK (SKIPPED / Deduplicated)
    else First-time Event
        Idem-->>Pipeline: false
        Pipeline->>SM: transition(VALIDATING)
        Pipeline->>Rec: diff(old_state, incoming_state)
        
        alt Conflict Found (e.g. Address changed while laptop SHIPPED)
            Rec-->>Pipeline: HireStateDiff(has_conflict=True)
            Pipeline->>SM: transition(CONFLICT_PAUSED)
            Pipeline->>Audit: log(CONFLICT_DETECTED)
            Pipeline->>AI: resolve(hire, diff, audit_history)
            AI-->>Pipeline: { action: "CANCEL_EQUIPMENT", escalate: true }
            Pipeline->>External: Slack #people-ops-alerts
            Pipeline->>Audit: log(CLAUDE_RESOLVED)
        else Standard Clean Change / New Hire
            Rec-->>Pipeline: HireStateDiff(has_conflict=False)
            Pipeline->>SM: transition(SYNCING)
            Pipeline->>Disp: dispatch(hire, diff)
            Disp->>External: Provision Okta + Lumos + Equipment
            Disp->>SM: complete_gate(...) & transition(SYNCED / DAY1_READY)
            Disp->>Audit: log(PROVISIONED / DAY1_READY)
        end
        
        Pipeline->>Idem: mark_seen(key, hire_id)
        Pipeline-->>Webhook: 200 OK (PROCESSED)
    end
```

---

### 3. State Machine Lifecycle (`HireState`)

```mermaid
stateDiagram-v2
    [*] --> DISCOVERED: Event Received
    DISCOVERED --> VALIDATING: Schema Check
    DISCOVERED --> FAILED: Catastrophic Payload Error

    VALIDATING --> PENDING_SYNC: Validated & Clean
    VALIDATING --> CONFLICT_PAUSED: Conflict Identified
    VALIDATING --> FAILED: Dead-Letter Queue

    PENDING_SYNC --> SYNCING: Dispatch In Progress
    PENDING_SYNC --> CONFLICT_PAUSED: Interlocking Gate Failure

    SYNCING --> SYNCED: All Connectors Updated
    SYNCING --> CONFLICT_PAUSED: Downstream API Rejection
    SYNCING --> FAILED: System Error

    CONFLICT_PAUSED --> PENDING_SYNC: AI/Human Resolved
    CONFLICT_PAUSED --> FAILED: Offer Rescinded / Unresolvable

    SYNCED --> DAY1_READY: All 7 Stage Gates Satisfied
    SYNCED --> PENDING_SYNC: Subsequent Update Received

    DAY1_READY --> PENDING_SYNC: Late Update Received (e.g., date push)

    FAILED --> [*]
    DAY1_READY --> [*]
```
