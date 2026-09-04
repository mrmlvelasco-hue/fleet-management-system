"""The configured default page size.

LIST_PAGES has existed as a System Parameter since the beginning --
seeded, shown in System Parameters, described as "Rows shown per page in
list screens" -- and NOTHING read it. Every list carried its own
hard-coded default (25 here, 50 there), so the setting looked
configurable and did nothing.

That is a direct breach of the master prompt's "no values shall be
hardcoded", and it is the reason the client's page length did not match
what the parameter said.

Cached per request via flask.g: a list endpoint should not issue an
extra SELECT for its own page size, and the value cannot change
mid-request.
"""
from flask import g

#: Used when the parameter is missing or unreadable. Matches the seeded
#: value so a database without the row behaves like one with it.
FALLBACK_PAGE_SIZE = 75

#: A ceiling regardless of configuration. Someone setting LIST_PAGES to
#: 10000 should get a large page, not a request that loads a quarter of
#: the table and times out -- and at a daily checklist per vehicle this
#: table reaches millions of rows.
MAX_PAGE_SIZE = 200


def default_page_size() -> int:
    cached = getattr(g, "_fms_list_pages", None)
    if cached is not None:
        return cached
    try:
        from app.modules.system_admin.services.system_parameter_service import (
            SystemParameterService)
        value = SystemParameterService().get("LIST_PAGES",
                                             default=FALLBACK_PAGE_SIZE)
        size = int(value)
        if size < 1:
            size = FALLBACK_PAGE_SIZE
    except Exception:
        # A misconfigured parameter must never take the list down. The
        # page still renders at the default and an administrator can fix
        # the value.
        size = FALLBACK_PAGE_SIZE
    size = min(size, MAX_PAGE_SIZE)
    try:
        g._fms_list_pages = size
    except RuntimeError:
        # No application context (a CLI call). Skip the cache.
        pass
    return size


def resolve_page_size(requested, maximum=MAX_PAGE_SIZE) -> int:
    """The page size for this request.

    An explicit per_page from the caller still wins -- an export or a
    mobile screen may legitimately want a different size -- but the
    DEFAULT now comes from the parameter instead of a literal.
    """
    if requested in (None, "", 0):
        return min(default_page_size(), maximum)
    try:
        size = int(requested)
    except (TypeError, ValueError):
        return min(default_page_size(), maximum)
    if size < 1:
        return min(default_page_size(), maximum)
    return min(size, maximum)
