"""AgentCore Runtime entry point — weekly Costco price match scan + SNS report."""

import logging
import os
import re
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
import boto3
from services.price_scanner import scan_price_drops
from services.analyzer import run_analysis

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

app = BedrockAgentCoreApp()

REGION = os.environ.get("AWS_REGION", "us-east-1")
S3_BUCKET = os.environ.get("S3_BUCKET", "")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")

s3 = boto3.client("s3", region_name=REGION)
sns = boto3.client("sns", region_name=REGION)


def _presign_links(text: str) -> str:
    def _replace(m):
        rid = m.group(1)
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": f"receipts/{rid}.pdf"},
            ExpiresIn=604800,
        )
        return f"]({url})"
    return re.sub(r"\]\(/api/receipt/([^/]+)/pdf\)", _replace, text)



@app.entrypoint
async def invoke(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        logging.info(f"Payload received: {payload}")
        deals = scan_price_drops(force_refresh=True)
        logging.info(f"Scanned {len(deals)} deals")

        report = run_analysis()
        logging.info(f"Analysis complete ({len(report)} chars)")

        email_report = _presign_links(report)
        logging.info(f"Report content:\n{email_report}")

        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject="Costco Weekly Price Match Report",
            Message=email_report,
        )
        logging.info("SNS report published")
        return {"status": "success", "deals_scanned": len(deals), "report": report}
    except Exception as e:
        logging.error(f"Error: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


app.run()
