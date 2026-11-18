# Studytomy report delivery recovery

The report scheduler is off by default. Enable `LEARNHOUSE_STUDYTOMY_REPORT_DELIVERY_ENABLED=true` only in the isolated test API for a synthetic rehearsal. Live operation requires named owners, approved disclosure policies, and service targets under TASK-069. No availability, response, or recovery target has been approved in this record.

## When an outbox lease expires

The next poll moves the expired lease to `reconciliation_required` with `LEASE_EXPIRED_OUTCOME_UNKNOWN`. The provider may already have accepted the message. The scheduler does not claim that row again, and the original sender cannot finalize the expired lease. Keep the row and its recorded outcome for review.

1. Check aggregate scheduler logs for reconciliation and dead-letter counts. Use the affected organization's authorized operational access to confirm the state. Keep recipient addresses, report contents, and private identifiers out of shared tickets and logs.
2. Preserve the pending record and refer it to the named operator. Confirm provider acceptance using approved provider records, if available. If the evidence is inconclusive, maintain the unknown outcome. Provider acceptance alone does not establish recipient delivery.
3. Before any authorized replacement notification, recheck the current report version, recipient authority, and purpose. The current scheduler has no operator reconciliation command for resolving an unknown provider outcome. Follow the approved incident workflow when available; changing the database row or restarting the scheduler cannot establish delivery.

The disposable PostgreSQL recovery test at `64cc4e5015dccde985ebe2a139efc944d9c2772d` passed all 13 concurrency tests. A fresh process handled an expired lease and the stale sender was refused. The lease deadline was simulated and no external provider was used. [Backup and restore evidence](MIGRATION-AND-ROLLBACK.md) covers the separate synthetic database rehearsal.
