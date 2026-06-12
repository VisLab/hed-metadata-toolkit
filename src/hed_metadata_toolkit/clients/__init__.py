"""clients — API client implementations for citation sources.

Clients:
  - crossref: CrossRef Works API (DOI lookup and query search)
  - openalex: OpenAlex Works API (search and DOI lookup)
  - europepmc: Europe PMC REST API (search and metadata)
  - pmc: PMC BioC API (full-text access for PubMed Central articles)
  - osf: Open Science Framework API (project/preprint metadata)
  - semanticscholar: Semantic Scholar Graph API (citation expansion and search)
  - unpaywall: Unpaywall API (open access status)

All clients use the shared cache layer (hed_metadata_toolkit.cache) for
network responses.
"""
