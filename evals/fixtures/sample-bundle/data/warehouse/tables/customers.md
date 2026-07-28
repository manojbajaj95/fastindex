---
type: BigQuery Table
title: Customers
description: One row per loyalty-enrolled customer.
when_to_use: Use when looking up loyalty customers, emails, or visit counts.
resource: https://console.cloud.google.com/bigquery?p=riverbend&d=pos&t=customers
tags: [pos, customers]
timestamp: 2026-05-28T00:00:00Z
---

# Schema

| Column | Type | Description |
| --- | --- | --- |
| `customer_id` | STRING | Square customer id. |
| `email` | STRING | Primary email on file. |
| `visit_count` | INT64 | Lifetime completed visits. |

Joined from [orders](/data/warehouse/tables/orders.md) on `customer_id`.
