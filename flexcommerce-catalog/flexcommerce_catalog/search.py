"""
Pluggable product search.

Point ``FLEXCOMMERCE["CATALOG_SEARCH_HANDLER"]`` at any function
``f(queryset, query) -> queryset`` to use Elasticsearch, Meilisearch, Typesense...
(e.g. fetch matching ids from the engine and ``return queryset.filter(pk__in=ids)``).
"""

from django.db.models import Q


def basic_search(queryset, query):
    """Database-agnostic search across name, descriptions, brand, SKU and tags."""
    terms = [t for t in query.split() if t][:8]
    for term in terms:
        queryset = queryset.filter(
            Q(name__icontains=term)
            | Q(short_description__icontains=term)
            | Q(description__icontains=term)
            | Q(brand__name__icontains=term)
            | Q(variants__sku__iexact=term)
        )
    return queryset.distinct()


def postgres_search(queryset, query):  # pragma: no cover - requires PostgreSQL
    """PostgreSQL full-text search with ranking (name weighted highest)."""
    from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

    vector = (
        SearchVector("name", weight="A")
        + SearchVector("brand__name", weight="B")
        + SearchVector("short_description", weight="B")
        + SearchVector("description", weight="C")
    )
    search_query = SearchQuery(query, search_type="websearch")
    return (
        queryset.annotate(search_rank=SearchRank(vector, search_query))
        .filter(search_rank__gt=0)
        .order_by("-search_rank")
    )
