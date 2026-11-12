#!/usr/bin/env bash
# Build the image, push it, and point the service and the ingestion job at it.
# Usage: scripts/deploy.sh <project-id> [region]
set -euo pipefail

PROJECT="${1:?usage: scripts/deploy.sh <project-id> [region]}"
REGION="${2:-europe-north1}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/disclosures/app:$(date +%Y%m%d-%H%M%S)"

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
docker build -t "$IMAGE" .
docker push "$IMAGE"

gcloud run jobs update disclosures-ingest --project "$PROJECT" --region "$REGION" --image "$IMAGE"
gcloud run deploy disclosures-api --project "$PROJECT" --region "$REGION" --image "$IMAGE"
gcloud run services describe disclosures-api --project "$PROJECT" --region "$REGION" \
  --format 'value(status.url)'
