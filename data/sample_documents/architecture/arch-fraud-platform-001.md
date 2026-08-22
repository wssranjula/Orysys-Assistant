---
fixture_id: arch-fraud-platform-001
title: Fraud Detection Platform Architecture
document_type: architecture
department: fraud
access_level: restricted
created_date: 2025-05-01
---

# Fraud Detection Platform Architecture

## Purpose and scope

The fraud platform evaluates payment events using rules, features, and model scores.

## Operational detail

Restricted case data is separated from general telemetry. Only fraud investigators and approved administrators may view raw case payloads.

## Controls and response

Models cannot initiate account actions directly; a policy service and human-review threshold gate interventions.

## Related evidence

See Fraud Investigation Playbook and Fraud Signals Specification.
