---
fixture_id: runbook-db-failover-001
title: Database Failover Runbook
document_type: runbook
department: technology
access_level: restricted
created_date: 2025-02-08
---

# Database Failover Runbook

## Purpose and scope

This runbook covers controlled promotion of the core banking recovery database.

## Operational detail

Confirm incident command approval, replication lag, last verified backup, recovery-region capacity, and application maintenance mode before promotion.

## Controls and response

Record the old primary timeline, fence writes, promote once, update trusted connection configuration, and validate balances with reconciliation queries.

## Related evidence

See Core Banking Disaster Recovery Architecture and incident CORE-515.
