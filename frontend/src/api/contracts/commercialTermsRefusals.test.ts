import { describe, expect, it } from "vitest";

// The backend's own source, as text — the mechanism `writeRefusals.test.ts` uses and for the same
// reasons: no `@types/node`, and an import is resolved at transform time, so a moved file fails the
// build instead of leaving the assertions below to compare against an empty string.
import COMMERCIAL_TERMS_API_PY from "../../../../backend/app/api/commercial_terms.py?raw";
import COMMERCIAL_TERMS_DATA_PY from "../../../../backend/app/data/commercial_terms.py?raw";
import COMMERCIAL_TERMS_MODEL_PY from "../../../../backend/app/models/commercial_terms.py?raw";

import {
  APPROVED_SCENARIO_MARKER,
  ONE_RULE_PER_SCENARIO_CONSTRAINT,
  SCENARIO_CHANGED_MARKER,
  commercialTermsRefusalCauseOf,
} from "./commercialTermsRefusals";

/**
 * SC-4-06, gate 1 decision D-3 (option b) — the commercial-rule write's refusals, written down twice.
 *
 * Two of the three `409`s this endpoint can answer carry no identifier: `_diagnose_refusal` in
 * `backend/app/data/commercial_terms.py` raises one of two exceptions with a sentence and nothing
 * else, and the API passes `str(refusal)` through as `detail`. The screen tells "approved — copy the
 * scenario" from "re-read" by a fragment of that sentence. That is only safe while the fragment is
 * still in the sentence, and nothing on either side would notice a rewording: the backend's tests
 * are written against the backend's classes, the screen's tests against the constants in
 * `commercialTermsRefusals.ts`. Hence this file, which reads the backend source rather than a
 * fixture — a fixture would be a third copy of the same guess.
 */

/** The message a backend exception class is constructed with in `_diagnose_refusal`, with the
 * adjacent Python string literals joined as Python joins them. `null` when the shape is gone. */
function messageRaisedAs(exceptionClass: string): string | null {
  const call = new RegExp(`return ${exceptionClass}\\(\\s*((?:"[^"]*"\\s*)+)\\)`).exec(
    COMMERCIAL_TERMS_DATA_PY,
  );
  if (call === null) {
    return null;
  }
  return [...call[1].matchAll(/"([^"]*)"/g)].map((literal) => literal[1]).join("");
}

describe("the commercial-rule refusal markers this client reads are the backend's own words", () => {
  it("reads the backend modules as text, so an assertion below is never about an empty string", () => {
    expect(COMMERCIAL_TERMS_DATA_PY).toContain("def _diagnose_refusal");
    expect(COMMERCIAL_TERMS_API_PY).toContain("HTTP_409_CONFLICT");
    expect(COMMERCIAL_TERMS_MODEL_PY).toContain("UniqueConstraint");
  });

  it("finds the approved-scenario marker in the message the backend raises for an approved scenario, and only there", () => {
    const frozen = messageRaisedAs("CommercialTermsFrozen");
    const changed = messageRaisedAs("CommercialTermsScenarioChanged");
    expect(frozen, "the backend no longer raises CommercialTermsFrozen in the shape read here").not
      .toBeNull();
    expect(changed).not.toBeNull();

    expect(frozen).toContain(APPROVED_SCENARIO_MARKER);
    expect(changed).not.toContain(APPROVED_SCENARIO_MARKER);
  });

  it("finds the scenario-changed marker in the message the backend raises for a changed scenario, and only there", () => {
    const frozen = messageRaisedAs("CommercialTermsFrozen");
    const changed = messageRaisedAs("CommercialTermsScenarioChanged");

    expect(changed).toContain(SCENARIO_CHANGED_MARKER);
    expect(frozen).not.toContain(SCENARIO_CHANGED_MARKER);
  });

  it("spells the one-rule-per-scenario constraint exactly as the model declares it", () => {
    // SC-4-05, D-3=A: the plain `UniqueConstraint("scenario_id", name="...")` SC-4-01 shipped
    // became a partial `Index(...)` (only `Index` carries `postgresql_where` in SQLAlchemy) —
    // same name, new shape. The name is now a module constant, not a string literal at the call
    // site, so this reads the call for the identifier and then resolves that identifier's own
    // assignment, rather than assuming either step alone spells the name out.
    const indexCall =
      /Index\(\s*(\w+),\s*"scenario_id",\s*unique=True,\s*postgresql_where=text\(SCOPE_REF_NULL_EXPRESSION\),?\s*\)/.exec(
        COMMERCIAL_TERMS_MODEL_PY,
      );
    expect(indexCall, "the model no longer declares the whole-scenario partial index in the shape read here")
      .not.toBeNull();
    const constantAssignment = indexCall
      ? new RegExp(`${indexCall[1]}\\s*=\\s*"([^"]+)"`).exec(COMMERCIAL_TERMS_MODEL_PY)
      : null;
    expect(
      constantAssignment,
      "the constant naming the whole-scenario index is no longer assigned a literal string here",
    ).not.toBeNull();
    expect(ONE_RULE_PER_SCENARIO_CONSTRAINT).toBe(constantAssignment?.[1]);
  });

  it("classifies each refusal built the way the backend builds it, and a refusal the backend cannot produce as unstated", () => {
    // The contrast that makes the assertions above mean something: these are the `detail` strings a
    // `409` really carries — the two prose refusals verbatim, and the database refusal assembled from
    // the API's prefix, the `describe_without_values` format and the model's constraint name.
    const frozen = messageRaisedAs("CommercialTermsFrozen") ?? "";
    const changed = messageRaisedAs("CommercialTermsScenarioChanged") ?? "";
    const prefix = /detail=f"(Refused by the database\.) \{refusal\}"/.exec(COMMERCIAL_TERMS_API_PY);
    expect(prefix, "the API no longer prefixes the database refusal in the shape read here").not
      .toBeNull();
    const subject = /subject="([^"]+)"/.exec(COMMERCIAL_TERMS_DATA_PY)?.[1];
    expect(subject).toBe("commercial terms");
    const duplicate =
      `${prefix?.[1]} Writing the ${subject} failed: IntegrityError, sqlstate=23505, ` +
      `constraint=${ONE_RULE_PER_SCENARIO_CONSTRAINT}`;

    expect(commercialTermsRefusalCauseOf(frozen)).toBe("approved");
    expect(commercialTermsRefusalCauseOf(changed)).toBe("scenario-changed");
    expect(commercialTermsRefusalCauseOf(duplicate)).toBe("rule-exists");
    // And the other direction: a refusal naming none of them is not quietly classified.
    expect(commercialTermsRefusalCauseOf("Refused by the database.")).toBe("unstated");
    expect(commercialTermsRefusalCauseOf(undefined)).toBe("unstated");
  });
});
