// Overwriting a customer's stored address automatically (FR-24).
//
// Near-certainty is required, and it must come from the landmark graph -- a
// coordinate learned from real deliveries -- not from a street geocoder or a
// pincode centroid, however confident the score looks.
@id("overwrite-needs-certainty")
permit(
  principal == Agent::"resolver",
  action == Action::"overwriteStoredAddress",
  resource
) when {
  context.confidence_pct > 95 &&
  context.geo_source == "landmark_graph"
};
