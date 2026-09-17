#!/usr/bin/env bash
# Smoke test. Run after EVERY deploy, no exceptions, including the 2 am one.
#
# A bad deploy should be caught here or by CI, never by a judge. Each assertion
# corresponds to something the demo actually depends on, so a failure here is
# always worth stopping for.
set -euo pipefail

STAGE="${STAGE:-dev}"
STACK="${STACK:-patasetu}"

API="${API_URL:-$(aws cloudformation describe-stacks \
  --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text)}"

echo "-> API: $API"

echo "-> health"
curl -sf "$API/health" | jq -e '.status == "ok"' > /dev/null
echo "   ok"

echo "-> resolve (clean address: pincode parsed AND gazetteer-validated)"
curl -sf -X POST "$API/v1/resolve" \
  -H 'content-type: application/json' \
  -d '{"raw":"h no 14 ramesh nagar delhi 110015"}' \
  | jq -e '.structured.pincode == "110015" and .structured.state == "Delhi"' > /dev/null
echo "   ok"

echo "-> resolve (landmark address: a 10-char DIGIPIN and a confidence)"
curl -sf -X POST "$API/v1/resolve" \
  -H 'content-type: application/json' \
  -d '{"raw":"behind shiv mandir near gupta store ramesh nagar delhi 110015"}' \
  | jq -e '.digipin != null and (.digipin | length) == 10 and .confidence > 0' > /dev/null
echo "   ok"

echo "-> resolve (Devanagari in, same pincode out)"
curl -sf -X POST "$API/v1/resolve" \
  -H 'content-type: application/json' \
  -d '{"raw":"शिव मंदिर के पीछे, रमेश नगर, दिल्ली 110015"}' \
  | jq -e '.structured.pincode == "110015"' > /dev/null
echo "   ok"

echo "-> PII: a phone number in the input must not appear anywhere in the output"
RESPONSE=$(curl -sf -X POST "$API/v1/resolve" \
  -H 'content-type: application/json' \
  -d '{"raw":"h no 14 ramesh nagar delhi 110015 call me 9876543210"}')
if grep -q "9876543210" <<< "$RESPONSE"; then
  echo "   FAIL: phone number leaked into the response" >&2
  exit 1
fi
echo "   ok"

echo "-> a malformed request is a 400, not a 500"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/v1/resolve" \
  -H 'content-type: application/json' -d '{"nope":1}')
if [[ "$CODE" != "400" ]]; then
  echo "   FAIL: expected 400, got $CODE" >&2
  exit 1
fi
echo "   ok"

# Extended in D3, once the Cedar authorizer is deployed:
#   -> with the clock at 23:00, contactCustomer returns DENY and the case
#      appears in the review queue with the matched policy id.

echo
echo "OK  smoke passed"
