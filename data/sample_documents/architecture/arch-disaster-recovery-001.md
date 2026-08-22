---
fixture_id: arch-disaster-recovery-001
title: Core Banking Disaster Recovery Architecture
document_type: architecture
department: technology
access_level: restricted
created_date: 2025-04-02
---

# Core Banking Disaster Recovery Architecture

## Purpose and scope

Core databases replicate synchronously within the primary region and asynchronously to the recovery region.

## Operational detail

Recovery targets are fifteen minutes of data and sixty minutes of service restoration. Promotion requires quorum from Technology, Operations, and Risk.

## Controls and response

Replication status, backup integrity, and application connection strings are validated before traffic moves.

## Related evidence

See Database Failover Runbook and incident CORE-515.
