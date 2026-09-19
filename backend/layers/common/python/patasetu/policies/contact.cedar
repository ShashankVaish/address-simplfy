// Who may contact a customer, and when.
//
// Policy is data, not code: this file is what an auditor reads, what the
// review queue cites by id when it explains a denial, and what a test asserts
// against. Changing it is a deploy of a text file, not a code change.
//
// Confidence travels as an integer percentage (`confidence_pct`, 0-100)
// because Cedar has no floating-point literals.

// Contacting a customer is permitted only under ALL of these conditions:
// the case is genuinely unresolved, nobody has messaged this order yet, it is
// a civil hour in the customer's local time, and the channel is one we run.
@id("contact-allowed-window")
permit(
  principal == Agent::"clarifier",
  action == Action::"contactCustomer",
  resource in OrderGroup::"pending_resolution"
) when {
  context.confidence_pct < 80 &&
  context.messages_sent_for_order == 0 &&
  context.local_hour >= 9 && context.local_hour <= 20 &&
  context.channel == "sms"
};

// Never, at any confidence, at any hour, by ANY principal, for an opted-out
// customer. A forbid beats every permit in Cedar, and this one deliberately
// does not name a principal: a test caught the first draft scoping it to the
// clarifier agent, which would have let a human operator override an opt-out.
@id("contact-opted-out")
forbid(
  principal,
  action == Action::"contactCustomer",
  resource in OrderGroup::"opted_out"
);

// A human operator may always reach out; the quiet-hours rule constrains the
// automated agent, not a person exercising judgement.
@id("contact-human-operator")
permit(
  principal == Agent::"operator",
  action == Action::"contactCustomer",
  resource in OrderGroup::"pending_resolution"
);
