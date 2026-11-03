def classify_ttl(content_type: str) -> str:
    """
    Dynamically determines the cache Time-To-Live (TTL) based on the content type.
    """
    content_type_lower = content_type.lower().strip()

    # News changes rapidly, keep cache short (24 hours)
    if "news" in content_type_lower:
        return "24h"
    # Hiring trends change slowly (72 hours)
    elif "hire" in content_type_lower or "hiring" in content_type_lower:
        return "72h"
    # Background or legal information is static (168 hours / 7 days)
    elif "background" in content_type_lower or "legal" in content_type_lower:
        return "168h"
    # Default fallback
    return "24h"


if __name__ == "__main__":
    print("Testing classifier.py locally:")
    print(f"News -> {classify_ttl('Latest News')}")
    print(f"Hires -> {classify_ttl('Recent Hires')}")
    print(f"Legal -> {classify_ttl('Legal Background')}")
