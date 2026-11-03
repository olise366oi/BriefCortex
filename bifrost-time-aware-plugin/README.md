# Bifrost Time-Aware Plugin (Content-Aware TTL Middleware)

## 💡 The Problem

Bifrost natively supports TTL (Time-To-Live) caching headers. However, its caching logic is static. It doesn't know if the incoming request is about breaking news (which expires in hours) or static legal background (which can be cached for weeks). In enterprise AI applications, this leads to wasted LLM token costs or outdated information.

## 🧠 Our Solution: Giving Bifrost a "Caching Strategy Brain"

We built a lightweight Python middleware that acts as an interceptor in front of Bifrost. It analyzes the business payload (the FactBase), classifies the content type, and dynamically injects the Content-Aware TTL and Dual-Layer Cache Key headers into the Bifrost request.

### Key Features:

1. **Dynamic TTL Classification:**
   - `News`: 24 hours (Fast-changing)
   - `Hires`: 72 hours (Slow-changing)
   - `Background/Legal`: 168 hours / 7 days (Static)
2. **Dual-Layer Cache Key Injection:**
   - Key format: `{company_id}_{purpose_hash}` (Ensures high cache reuse for the same meeting purpose, while avoiding cache pollution across different meeting intents).
3. **Zero Intrusion:**
   - By injecting standard HTTP headers (`x-bf-cache-key`, `x-bf-cache-ttl`), we extend Bifrost's capabilities without modifying a single line of its native Go source code.

## 🏆 Enterprise Value

For enterprise AI applications, data freshness is a cost center. By dynamically setting TTLs based on data volatility, our middleware allows enterprises to achieve intelligent cost reduction: caching static data as long as possible to save API calls, while aggressively updating fast-changing facts to ensure accurate business intelligence.

---
*Reference Implementation: This plugin is built to power BriefCortex (Track 02), an AI pre-meeting intelligence agent.*
