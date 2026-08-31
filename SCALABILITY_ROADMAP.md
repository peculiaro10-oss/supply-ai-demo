# Cauldra scalability roadmap

## Now

Use PostgreSQL, Alembic, conservative connection pooling, tenant-scoped indexes,
private storage abstraction, backup/restore testing, and application/database
health monitoring. Do not introduce Redis, workers, replicas, partitioning,
sharding, Kubernetes, or microservices without measured need.

## Around 100 active businesses

Measure p95 request latency, slow queries, connection-pool usage, database CPU/
I/O, error rate, upload growth, and AI latency. Evaluate caching or a job queue
only if those measurements show a bottleneck.

## Around 500 active businesses

Load-test multiple stateless app instances behind a load balancer. Move uploads
to private S3-compatible storage before using more than one application instance.
Consider dedicated background workers only for measured long-running work.

## Around 1,000 active businesses

Use PostgreSQL slow-query logs and execution plans to tune proven bottlenecks.
Review autovacuum, connection limits, audit/sales/history table growth, and AI
workload contention before considering workload isolation or caching.

## Around 5,000+ active businesses

Evaluate read replicas, stronger caching, partitioning of demonstrably large
history tables, and higher-availability database operations only from production
metrics and recovery objectives.

## Around 10,000+ active businesses

Evaluate dedicated database infrastructure, AI workers, centralized observability,
edge/CDN delivery, regional resilience, and capacity planning. These are decision
points, not automatic architecture changes.
