#!/usr/bin/env bash
# Run every night before sleeping.
#
# OpenSearch Serverless bills on provisioned capacity by the hour, idle or not.
# Left running across three nights it is the difference between finishing the
# weekend with credits and without.
#
# Only the collection is torn down. Everything else in the stack -- Lambda,
# DynamoDB on-demand, S3, the HTTP API -- scales to zero and costs nothing idle,
# so the public URL stays up for anyone who wants to try it overnight.
set -euo pipefail

STAGE="${STAGE:-dev}"
COLLECTION="${COLLECTION:-patasetu-$STAGE}"

ID=$(aws opensearchserverless list-collections \
  --query "collectionSummaries[?name=='$COLLECTION'].id" \
  --output text 2>/dev/null || true)

if [[ -z "$ID" || "$ID" == "None" ]]; then
  echo "no collection named $COLLECTION -- nothing to tear down"
  exit 0
fi

echo "deleting OpenSearch Serverless collection $COLLECTION ($ID)"
aws opensearchserverless delete-collection --id "$ID"

echo
echo "Torn down. Bring it back when work restarts:"
echo "  python -m scripts.create_index"
echo "  python -m scripts.warm_landmarks eval/data/corpus.jsonl"
