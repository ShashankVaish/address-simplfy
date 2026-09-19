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

# --- D3: the Cedar path, end to end ------------------------------------------
# Operator routes may be behind Cognito (ProtectOperatorRoutes=true). Pass an
# id token in OPERATOR_TOKEN and the calls below carry it.
# macOS ships bash 3.2, where expanding an empty array under `set -u` is an
# "unbound variable" error, so the header is a plain string, not an array.
AUTH_HEADER="x-smoke: 1"
if [[ -n "${OPERATOR_TOKEN:-}" ]]; then AUTH_HEADER="authorization: Bearer $OPERATOR_TOKEN"; fi
ORDER="SMOKE-$(date +%s)"

echo "-> Cedar: clock at 23:00, contactCustomer must be DENY, with a reason"
DECISION=$(curl -sf -X POST "$API/v1/authorize" -H "$AUTH_HEADER"   -H 'content-type: application/json'   -d "{\"order_id\":\"$ORDER\",\"confidence\":0.55,\"local_hour\":23,\"summary\":\"smoke\"}")
jq -e '.decision == "DENY" and (.reason | test("outside contact hours"))' <<< "$DECISION" > /dev/null
echo "   ok"

echo "-> Cedar: opted-out customer is DENY naming the forbid policy"
curl -sf -X POST "$API/v1/authorize" -H "$AUTH_HEADER"   -H 'content-type: application/json'   -d "{\"order_id\":\"$ORDER-opt\",\"confidence\":0.55,\"local_hour\":14,\"opted_out\":true}"   | jq -e '.decision == "DENY" and .matched_policies == ["contact-opted-out"]' > /dev/null
echo "   ok"

echo "-> Cedar: same order at 14:00 is ALLOW"
curl -sf -X POST "$API/v1/authorize" -H "$AUTH_HEADER"   -H 'content-type: application/json'   -d "{\"order_id\":\"$ORDER-day\",\"confidence\":0.55,\"local_hour\":14}"   | jq -e '.decision == "ALLOW" and .matched_policies == ["contact-allowed-window"]' > /dev/null
echo "   ok"

echo "-> the denied case is in the review queue with the Cedar reason"
curl -sf "$API/v1/queue?limit=100" -H "$AUTH_HEADER"   | jq -e --arg id "$ORDER" '.items[] | select(.order_id == $id) | .review_reason | test("Cedar DENY")' > /dev/null
echo "   ok"

echo
echo "OK  smoke passed"
