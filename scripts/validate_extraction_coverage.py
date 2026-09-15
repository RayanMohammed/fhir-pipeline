"""
Answers a question the test suite alone can't: for the real, actual archive
this pipeline ingests, did extract_clinical_data() capture everything it was
supposed to, or did something silently fall through?

test_extraction.py proves the parsing functions are correct against a
handful of hand-built fixtures. This script is a different kind of check:
it walks the real source bundles a second, completely independent time --
never calling into shared/extraction.py's helpers, just reading the same
raw JSON with its own eyes -- and compares what it finds against what
extract_clinical_data() (the real, unmodified function) actually returned
for the same bundle. If the two disagree, that's a real finding, not a
guess.

Every in-scope Observation/Condition resource lands in exactly one bucket:
  - captured           -- exists in source, extraction produced it. Healthy.
  - dropped            -- in scope by LOINC code, but extraction produced
                           nothing for it. This is the bucket that matters --
                           it means something is actually broken.
  - out_of_scope       -- a real clinical resource this pipeline was never
                           designed to capture (this project only ever
                           parses height, weight, and blood pressure).
                           Not a bug -- just previously invisible.

Bundle-level failures (extract_clinical_data() returning None entirely --
missing Patient, malformed structure) are NOT re-litigated here; that's
already batch_ingest.py's dead-letter queue's job. This script only asks:
for bundles that DID extract successfully, was that success complete?
"""
import argparse
import io
import json
import os
import tarfile
import uuid
from collections import Counter

import boto3

from shared.extraction import extract_clinical_data, VITAL_CODES
from scripts.stage_synthea_batch import NON_PATIENT_PREFIXES
from worker.batch_ingest import DEFAULT_ARCHIVE_KEY

R2_ENDPOINT_URL = os.getenv("R2_ENDPOINT_URL")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "clinical-data-lake")

BP_PANEL_CODE = "85354-9"
BP_COMPONENT_CODES = {"8480-6", "8462-4"}  # systolic, diastolic


def parse_args():
    parser = argparse.ArgumentParser(
        description="Independently verify extract_clinical_data() against a real archive."
    )
    parser.add_argument(
        "--archive-key", default=DEFAULT_ARCHIVE_KEY,
        help="R2 object key of the .tar.gz archive to check (default: the original archive).",
    )
    parser.add_argument(
        "--output-dir", default="extraction-coverage-results",
        help="Directory to write the coverage report and supporting CSVs into.",
    )
    return parser.parse_args()


def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def resource_primary_code(resource: dict) -> str | None:
    """
    The first coded value on a resource's `code.coding` list.
    Synthea output has exactly one relevant coding per Observation,
    so first and meaningful are the same here.
    """
    for coding in (resource.get("code") or {}).get("coding", []):
        code = coding.get("code")
        if code:
            return code
    return None


def check_vital_resource(resource: dict, captured_observations: list[dict]):
    """
    Height/weight: _extract_vital() preserves the source `id` 
    as the output row's id, so this is a direct id match.
    """
    obs_id = resource.get("id")
    obs_date = resource.get("effectiveDateTime")
    if not obs_id or not obs_date:
        return "dropped", "source resource is missing id or effectiveDateTime"

    quantity = resource.get("valueQuantity") or {}
    if quantity.get("value") is None:
        return "dropped", "source resource has no valueQuantity.value"

    try:
        expected_id = uuid.UUID(str(obs_id))
    except ValueError:
        return "dropped", f"source resource id '{obs_id}' is not a valid UUID"

    was_captured = any(row["id"] == expected_id for row in captured_observations)
    if was_captured:
        return "captured", None
    return "dropped", "resource had a valid id/date/value but no matching row exists in extraction output"


def check_bp_resource(resource: dict, captured_observations: list[dict]):
    """
    Blood pressure: _extract_bp_components() does NOT reuse the source
    resource's id. It derives two new, deterministic ids thru
    uuid.uuid5(uuid.NAMESPACE_OID, f"{obs_id}:{comp_code}"), one per
    component. Recomputing that same formula here is how this script 
    checks for those derived ids independently.
    """
    obs_id = resource.get("id")
    obs_date = resource.get("effectiveDateTime")
    if not obs_id or not obs_date:
        return "dropped", "source resource is missing id or effectiveDateTime"

    expected_component_codes = set()
    for component in resource.get("component", []):
        comp_quantity = component.get("valueQuantity") or {}
        if comp_quantity.get("value") is None:
            continue
        for coding in (component.get("code") or {}).get("coding", []):
            if coding.get("code") in BP_COMPONENT_CODES:
                expected_component_codes.add(coding.get("code"))

    if not expected_component_codes:
        return "dropped", "no usable systolic/diastolic component values on the source resource"

    missing = []
    for comp_code in expected_component_codes:
        expected_id = uuid.uuid5(uuid.NAMESPACE_OID, f"{obs_id}:{comp_code}")
        if not any(row["id"] == expected_id for row in captured_observations):
            missing.append(comp_code)

    if missing:
        return "dropped", f"missing expected component row(s) for code(s): {sorted(missing)}"
    return "captured", None


def check_condition_resource(resource: dict, captured_conditions: list[dict]):
    """
    Conditions: _extract_condition() also preserves the resource's own
    id, same direct-match approach as the vitals check.
    """
    condition_id = resource.get("id")
    onset = resource.get("onsetDateTime")
    codings = (resource.get("code") or {}).get("coding", [])

    if not condition_id or not onset:
        return "dropped", "source resource is missing id or onsetDateTime"
    if not codings or not codings[0].get("code"):
        return "dropped", "source resource has no coded value"

    try:
        expected_id = uuid.UUID(str(condition_id))
    except ValueError:
        return "dropped", f"source resource id '{condition_id}' is not a valid UUID"

    was_captured = any(row["id"] == expected_id for row in captured_conditions)
    if was_captured:
        return "captured", None
    return "dropped", "resource had a valid id/onset/code but no matching row exists in extraction output"


def reconcile_bundle(bundle_dict: dict, patient_label: str, totals: dict):
    """
    Walks thru the raw bundle once, resource by resource, classifying 
    every Observation and Condition, and folds the result into the 
    archive-wide `totals` dict shared across every bundle.
    Assumes extract_clinical_data() already succeeded for this bundle.
    """
    extracted = extract_clinical_data(bundle_dict)
    captured_observations = extracted["observations"]
    captured_conditions = extracted["conditions"]
    patient_id = extracted["patient"]["id"]

    # Every row this bundle produced should belong to this bundle's one patient.
    for row in captured_observations + captured_conditions:
        if str(row["patient_id"]) != str(patient_id):
            totals["misattributed"].append((patient_label, row["id"]))

    for entry in bundle_dict.get("entry", []):
        resource = entry.get("resource")
        if not isinstance(resource, dict) or resource.get("resourceType") != "Observation":
            continue

        code = resource_primary_code(resource)
        if code in VITAL_CODES:
            status, reason = check_vital_resource(resource, captured_observations)
        elif code == BP_PANEL_CODE:
            status, reason = check_bp_resource(resource, captured_observations)
        else:
            totals["out_of_scope_codes"][code or "(no code)"] += 1
            continue

        totals["in_scope_total"] += 1
        if status == "captured":
            totals["in_scope_captured"] += 1
        else:
            totals["in_scope_dropped"].append((patient_label, resource.get("id"), code, reason))

    for entry in bundle_dict.get("entry", []):
        resource = entry.get("resource")
        if not isinstance(resource, dict) or resource.get("resourceType") != "Condition":
            continue
        totals["conditions_total"] += 1
        status, reason = check_condition_resource(resource, captured_conditions)
        if status == "captured":
            totals["conditions_captured"] += 1
        else:
            totals["conditions_dropped"].append((patient_label, resource.get("id"), reason))


def write_report(totals: dict, output_dir: str, archive_key: str, bundle_count: int, extraction_failures: int):
    """
    Writes a Markdown summary and two supporting CSVs into `output_dir`.
    """
    os.makedirs(output_dir, exist_ok=True)

    in_scope_pct = (
        100 * totals["in_scope_captured"] / totals["in_scope_total"]
        if totals["in_scope_total"] else 100.0
    )
    condition_pct = (
        100 * totals["conditions_captured"] / totals["conditions_total"]
        if totals["conditions_total"] else 100.0
    )

    lines = [
        "# Extraction coverage report",
        "",
        f"Archive checked: `{archive_key}`",
        f"Patient bundles scanned: {bundle_count} ({extraction_failures} failed extraction entirely -- handled by the DLQ, not counted below)",
        "",
        "## In-scope Observations (height, weight, blood pressure)",
        f"- Total found in source: {totals['in_scope_total']}",
        f"- Captured by extract_clinical_data(): {totals['in_scope_captured']} ({in_scope_pct:.1f}%)",
        f"- Unexpectedly dropped: {len(totals['in_scope_dropped'])}",
        "",
        "## Conditions",
        f"- Total found in source: {totals['conditions_total']}",
        f"- Captured: {totals['conditions_captured']} ({condition_pct:.1f}%)",
        f"- Unexpectedly dropped: {len(totals['conditions_dropped'])}",
        "",
        "## Misattribution check",
        f"- Rows found attributed to the wrong patient: {len(totals['misattributed'])}",
        "",
        "## Out-of-scope Observation codes (real clinical data this pipeline doesn't parse)",
        f"- Distinct out-of-scope codes seen: {len(totals['out_of_scope_codes'])}",
        f"- Total out-of-scope resources seen: {sum(totals['out_of_scope_codes'].values())}",
        "- See `out_of_scope_codes.csv` for the full per-code breakdown.",
        "",
    ]
    if totals["in_scope_dropped"] or totals["conditions_dropped"] or totals["misattributed"]:
        lines.append("See `dropped_resources.csv` for every unexpected drop, with patient/resource ids and reasons.")
    else:
        lines.append("No unexpected drops or misattributions found.")

    with open(os.path.join(output_dir, "coverage_report.md"), "w") as f:
        f.write("\n".join(lines) + "\n")

    with open(os.path.join(output_dir, "out_of_scope_codes.csv"), "w") as f:
        f.write("loinc_code,count\n")
        for code, count in totals["out_of_scope_codes"].most_common():
            f.write(f"{code},{count}\n")

    with open(os.path.join(output_dir, "dropped_resources.csv"), "w") as f:
        f.write("patient,resource_id,code_or_reason,reason\n")
        for patient_label, resource_id, code, reason in totals["in_scope_dropped"]:
            f.write(f"{patient_label},{resource_id},{code},{reason}\n")
        for patient_label, resource_id, reason in totals["conditions_dropped"]:
            f.write(f"{patient_label},{resource_id},condition,{reason}\n")
        for patient_label, resource_id in totals["misattributed"]:
            f.write(f"{patient_label},{resource_id},misattributed,row's patient_id did not match its own bundle\n")

    print("\n".join(lines))
    print(f"\nFull report written to {output_dir}/")


def main():
    args = parse_args()
    s3 = get_r2_client()

    print(f"Fetching {args.archive_key} from R2...")
    response = s3.get_object(Bucket=R2_BUCKET_NAME, Key=args.archive_key)
    byte_stream = io.BytesIO(response["Body"].read())

    totals = {
        "in_scope_total": 0,
        "in_scope_captured": 0,
        "in_scope_dropped": [],
        "conditions_total": 0,
        "conditions_captured": 0,
        "conditions_dropped": [],
        "out_of_scope_codes": Counter(),
        "misattributed": [],
    }
    bundle_count = 0
    extraction_failures = 0

    with tarfile.open(fileobj=byte_stream, mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            filename = os.path.basename(member.name)
            if filename.startswith(NON_PATIENT_PREFIXES) or filename.startswith("."):
                continue

            extracted_file = tar.extractfile(member)
            if not extracted_file:
                continue

            try:
                bundle_dict = json.loads(extracted_file.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                extraction_failures += 1
                continue

            if extract_clinical_data(bundle_dict) is None:
                extraction_failures += 1
                continue

            bundle_count += 1
            reconcile_bundle(bundle_dict, patient_label=filename, totals=totals)

    write_report(totals, args.output_dir, args.archive_key, bundle_count, extraction_failures)


if __name__ == "__main__":
    main()
