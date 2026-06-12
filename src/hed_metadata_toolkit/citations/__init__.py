"""citations — Citation workflow modules.

Workflow stages:
  - collect_citations: Extract raw citation links from dataset files
  - assign_citation_ids: Idempotent, permanent citation-ID assignment
  - enrich_pub_ids: Assign pub_ids to citation registry rows via API lookups
  - apply_manual_fills: Bank curator JSON decisions into the citation registry
  - generate_review_queue: Emit JSON template for manual curator review
"""
