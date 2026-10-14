---
id: correlation_detect
version: 1
description: AI auto-correlation — find dynamic values in a batch of HTTP traffic (JSON array answer)
---
<<<SYSTEM>>>
<<<USER>>>
You are a JMeter performance test expert. Analyze this HTTP traffic and identify ALL dynamic values that need correlation — values produced by one response and consumed in later requests.

For each dynamic value found, return a JSON object with:
  "source_request_index"    : integer — index of the response that contains the value
  "field_name"              : string  — JSON key, header name, or descriptive name
  "location"                : "body" | "header" | "cookie"
  "value"                   : string  — the actual dynamic value (truncated to 80 chars)
  "used_in_request_indices" : array of integers — which later requests use this value
  "reason"                  : string  — one sentence explaining why it needs correlation

Focus on:
- Bearer tokens / JWTs / access_token / refresh_token
- Session IDs, CSRF tokens, nonces
- Entity IDs (user IDs, order IDs, UUIDs) returned in a response then used in later URLs or bodies
- Any value that changes per session and appears in multiple requests

HTTP Traffic:
{{traffic}}

Return ONLY a valid JSON array. If nothing needs correlation, return: []

JSON array:
