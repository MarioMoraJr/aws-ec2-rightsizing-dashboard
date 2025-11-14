# lambda_function.py
import json, boto3, datetime, uuid, random, hashlib
from decimal import Decimal
from botocore.exceptions import ClientError

# --- REAL RESOURCES ---
BUCKET = "mario-cloud-portfolio"
PREFIX = "projects/ec2-rightsizing"
CF_DIST_ID = "EUJMCTXUE2NVA"

# --- Real-data sources config
RECOMMENDATION_TARGET = "CROSS_INSTANCE_FAMILY"  # for Cost Explorer
BENEFITS_CONSIDERED = True
MIN_SAVINGS = 0.01  # if less than this, we switch to synthetic

ce = boto3.client("ce")
co = boto3.client("compute-optimizer")
ec2 = boto3.client("ec2")
s3 = boto3.client("s3")
cf = boto3.client("cloudfront")

# ------------ helpers
def _iso_now():
    return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat()

def _put_json(obj, key):
    body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="application/json",
        CacheControl="public, max-age=60, must-revalidate",
    )

def _sum_ce_savings(recs):
    total = 0.0
    for r in recs:
        # CE recs can be Modify or Terminate; both have EstimatedMonthlySavings
        detail = r.get("ModifyRecommendationDetail") or r.get("TerminateRecommendationDetail") or {}
        savings = detail.get("EstimatedMonthlySavings", {})
        amt = savings.get("Amount")
        try:
            total += float(amt) if amt is not None else 0.0
        except Exception:
            pass
    return total

def _sum_co_savings(recs):
    total = 0.0
    for r in recs:
        savings = r.get("savingsOpportunity", {}).get("estimatedMonthlySavings", {})
        val = savings.get("value")
        try:
            total += float(val) if val is not None else 0.0
        except Exception:
            pass
    return total

# ------------ real data fetchers
def _fetch_ce_rightsizing():
    next_token = None
    recs = []
    summary = {}
    while True:
        args = {
            "Service": "AmazonEC2",
            "Configuration": {
                "RecommendationTarget": RECOMMENDATION_TARGET,
                "BenefitsConsidered": BENEFITS_CONSIDERED,
            },
            "PageSize": 20,
        }
        if next_token:
            args["NextPageToken"] = next_token
        resp = ce.get_rightsizing_recommendation(**args)
        recs.extend(resp.get("RightsizingRecommendations", []))
        summary = resp.get("Summary", summary)
        next_token = resp.get("NextPageToken")
        if not next_token:
            break
    return summary, recs

def _fetch_co_rightsizing():
    next_token = None
    recs = []
    while True:
        args = {"maxResults": 100}
        if next_token:
            args["nextToken"] = next_token
        resp = co.get_ec2_instance_recommendations(**args)
        recs.extend(resp.get("instanceRecommendations", []))
        next_token = resp.get("nextToken")
        if not next_token:
            break
    # status is informative only
    try:
        status = co.get_enrollment_status().get("status", "Unknown")
    except ClientError:
        status = "Unknown"
    summary = {"compute_optimizer_enrollment_status": status}
    return summary, recs

def _any_running_instances():
    try:
        resp = ec2.describe_instances(
            Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
        )
        for res in resp.get("Reservations", []):
            if res.get("Instances"):
                return True
    except ClientError:
        pass
    return False

# ------------ synthetic generator (CE-shaped objects)
FAMILIES = [
    ("t3", ["micro","small","medium","large","xlarge","2xlarge"]),
    ("t4g", ["small","medium","large","xlarge","2xlarge"]),
    ("m6i", ["large","xlarge","2xlarge"]),
    ("m7i", ["large","xlarge","2xlarge"]),
    ("c7g", ["large","xlarge","2xlarge"]),
    ("r6i", ["large","xlarge","2xlarge"]),
]

def _daily_seed():
    # stable randomness per day so reruns the same day don't thrash
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    h = hashlib.sha256(today.encode("utf-8")).hexdigest()
    return int(h[:16], 16)

def _pick_instance_type():
    fam, sizes = random.choice(FAMILIES)
    return f"{fam}.{random.choice(sizes)}"

def _smaller_type(of_type):
    fam, size = of_type.split(".")
    order = ["micro","small","medium","large","xlarge","2xlarge","4xlarge","8xlarge"]
    try:
        idx = max(order.index(size), 1)
        new_size = order[idx-1]
    except ValueError:
        new_size = "small"
    # maybe offer a graviton swap variant sometimes
    if fam.startswith("m6i") and random.random() < 0.5:
        fam = "m7g"
    if fam.startswith("t3") and random.random() < 0.5:
        fam = "t4g"
    return f"{fam}.{new_size}"

def _rand_instance_id():
    return "i-" + "".join(random.choices("0123456789abcdef", k=17))

def _gen_synthetic_recs():
    random.seed(_daily_seed())
    n = random.randint(2, 5)
    recs = []
    total = 0.0
    for _ in range(n):
        current_type = _pick_instance_type()
        target_type = _smaller_type(current_type)
        rightsizing_type = "Modify" if random.random() > 0.25 else "Terminate"

        monthly_savings = round(random.uniform(3.0, 120.0), 2)
        total += monthly_savings

        if rightsizing_type == "Modify":
            recs.append({
                "AccountId": "913979368763",
                "CurrentInstance": {
                    "ResourceId": _rand_instance_id(),
                    "InstanceName": f"app-{random.randint(10,99)}",
                    "ResourceDetails": {"EC2ResourceDetails": {"InstanceType": current_type}},
                },
                "RightsizingType": "Modify",
                "ModifyRecommendationDetail": {
                    "TargetInstances": [
                        {
                            "EstimatedMonthlySavings": {"Amount": f"{monthly_savings:.2f}", "Unit": "USD"},
                            "ResourceDetails": {"EC2ResourceDetails": {"InstanceType": target_type}},
                            "ExpectedCost": {"Amount": "—", "Unit": "USD"},
                        }
                    ],
                    "EstimatedMonthlySavings": {"Amount": f"{monthly_savings:.2f}", "Unit": "USD"}
                }
            })
        else:
            recs.append({
                "AccountId": "913979368763",
                "CurrentInstance": {
                    "ResourceId": _rand_instance_id(),
                    "InstanceName": f"batch-{random.randint(10,99)}",
                    "ResourceDetails": {"EC2ResourceDetails": {"InstanceType": current_type}},
                },
                "RightsizingType": "Terminate",
                "TerminateRecommendationDetail": {
                    "EstimatedMonthlySavings": {"Amount": f"{monthly_savings:.2f}", "Unit": "USD"}
                }
            })

    summary = {
        "TotalEstimatedMonthlySavingsAmount": f"{total:.2f}",
        "TotalEstimatedMonthlySavingsCurrency": "USD",
        "EstimatedSavingsPercentage": str(round(random.uniform(5.0, 55.0), 2))
    }
    return summary, recs

# ------------ handler
def lambda_handler(event, context):
    source = None
    summary = {}
    recs = []

    # 1) Try Cost Explorer
    try:
        summary, recs = _fetch_ce_rightsizing()
        source = "cost-explorer"
        if _sum_ce_savings(recs) < MIN_SAVINGS:
            raise RuntimeError("CE savings below threshold")
    except Exception:
        # 2) Try Compute Optimizer
        try:
            summary, recs = _fetch_co_rightsizing()
            source = "compute-optimizer"
            if _sum_co_savings(recs) < MIN_SAVINGS:
                raise RuntimeError("CO savings below threshold")
        except Exception:
            # 3) If no real signal, check if anything is even running; if not, synthesize
            if not _any_running_instances():
                summary, recs = _gen_synthetic_recs()
                source = "synthetic"
            else:
                # running but no savings signal -> still synthesize to keep portfolio lively
                summary, recs = _gen_synthetic_recs()
                source = "synthetic"

    payload = {
        "generated_at": _iso_now(),
        "source": source,  # cost-explorer | compute-optimizer | synthetic
        "recommendation_target": RECOMMENDATION_TARGET if source == "cost-explorer" else None,
        "benefits_considered": BENEFITS_CONSIDERED if source == "cost-explorer" else None,
        "summary": summary,
        "count": len(recs),
        "recommendations": recs
    }

    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    dated_key = f"{PREFIX}/{today}.json"
    latest_key = f"{PREFIX}/latest.json"

    _put_json(payload, dated_key)
    _put_json(payload, latest_key)

    cf.create_invalidation(
        DistributionId=CF_DIST_ID,
        InvalidationBatch={
            "Paths": {"Quantity": 1, "Items": [f"/{latest_key}"]},
            "CallerReference": f"rightsizing-{uuid.uuid4()}"
        }
    )

    return {"status": "ok", "source": source, "dated_key": dated_key, "latest_key": latest_key, "items": len(recs)}
