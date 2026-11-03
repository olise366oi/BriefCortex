import hashlib
from typing import Dict, Any
from classifier import classify_ttl


def generate_bifrost_headers(company_id: str, meeting_purpose: str, fact_base_dict: Dict[str, Any]) -> Dict[str, str]:
    """
    Generates dynamic Bifrost caching headers based on meeting purpose and fact base content.
    """
    # 1. Generate a deterministic hash for the meeting purpose to ensure cache reusability
    purpose_hash = hashlib.md5(meeting_purpose.encode('utf-8')).hexdigest()[:8]

    # 2. Simple content analysis to determine the primary content type
    content_str = str(fact_base_dict).lower()
    content_type = "news"  # Default

    # Use substring matching to capture hire, hires, hiring
    hires_count = content_str.count("hir")
    news_count = content_str.count("news") + content_str.count("announced")

    if hires_count >= news_count and hires_count > 0:
        content_type = "hires"
    elif "legal" in content_str or "background" in content_str:
        content_type = "background"

    # 3. Calculate TTL
    ttl = classify_ttl(content_type)

    # 4. Return the required Bifrost headers
    return {
        "x-bf-cache-key": f"{company_id}_{purpose_hash}",
        "x-bf-cache-ttl": ttl
    }


if __name__ == "__main__":
    print("Testing middleware.py locally:")
    mock_fact_base = {"data": "NIO is hiring a new CFO and announced layoffs."}
    headers = generate_bifrost_headers("NIO", "Sourcing logistics services", mock_fact_base)
    print("Generated Headers:", headers)
