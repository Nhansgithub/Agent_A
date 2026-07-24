# final_PRD_Ingestion Pipeline

## Architecture
The ingestion service consumes from a Kafka topic, deserializes Avro records, and writes to the
warehouse via a batched COPY. We shard by tenant id and use a dead-letter topic for poison messages.

## Sequence
1. Consumer polls the topic in batches of 500.
2. Records are validated against the registry schema.
3. Valid records buffer until 5s or 10k rows, then flush.

## Failure modes
- Schema mismatch -> dead-letter.
- Warehouse unavailable -> pause the consumer, retry with backoff.

## Deployment
Two replicas per region behind the existing Helm chart; autoscale on consumer lag.
