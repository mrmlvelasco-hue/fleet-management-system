"""Request-scoped memoisation for expensive, read-only calculations.

The dashboard was calling the SAME heavy due-calculation twice per page
load -- once for the KPI card's count and again to build the list right
underneath it -- for both Maintenance and Registration. Four full passes
over the fleet where one each would do.

Each pass rebuilds an in-memory snapshot of every active PM schedule
(4,600+ rows on a fully-imported catalogue) and then matches every
vehicle against it, so the duplication is not a rounding error: it is
literally double the most expensive thing the page does.

These results must NOT be cached across requests -- a vehicle's odometer
or a completed order changes the answer, and showing yesterday's due
list would be worse than showing it slowly. So the cache lives on
`flask.request` itself, not on `flask.g`.

That distinction matters and was the source of a real, confirmed bug.
`g` is scoped to the APPLICATION context, and Flask deliberately allows
one app context to span several requests -- which is exactly what this
project's own test fixture does (`with app.app_context(): yield app`
around the whole test), and what a long-running script or a Celery task
does too. Confirmed directly: two separate `test_client().get()` calls
made inside one such held-open app context produced the literal same
`id(g)` both times, so the second, genuinely different request was
silently served the first request's cached value.

`flask.request`, by contrast, is pushed fresh for every request context
with no exceptions -- that is what actually distinguishes a request
context from an app context in Flask's two-tier model. Also confirmed
directly: the same two calls produced two different request objects,
with no attribute set on the first carried onto the second.

An earlier version tried to compensate for the `g` problem by comparing
`id(flask.request)` against a value stamped onto the `g`-based store on
the previous call. That was worse, not better: `id()` is a memory
address, and CPython is free to reuse a freed object's address for a
completely different object once the first is garbage collected. Under
light load two requests' addresses never collided, which is why this
passed every time run in isolation; under a full suite's sustained
allocation churn a torn-down request's address occasionally WAS
recycled for the next one, making two genuinely different requests look
identical to the id() check and reproducing the exact same stale-cache
bug one level removed. Storing on `request` directly removes the need
for any identity comparison at all -- there's nothing to compare against
that can go stale, because the object itself never outlives its request.

Outside a request context (CLI commands, Celery tasks, tests) it falls
straight through to the real function with no caching, so behaviour is
identical there.
"""
import functools

from flask import has_request_context, request


def request_cached(key: str):
    """Memoise a zero-argument-ish call for the life of one request.

    `key` must be unique per logical result. Arguments are folded into
    the cache key so that, e.g., a per-user scoped call and an unscoped
    call don't collide.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not has_request_context():
                return fn(*args, **kwargs)

            # Skip `self` when building the key: two instances of a
            # stateless service should share a cached result, and
            # including the instance would defeat the whole point since
            # the callers construct a fresh service each time.
            key_args = args[1:] if args else ()
            try:
                cache_key = (key, key_args,
                            tuple(sorted(kwargs.items())))
                hash(cache_key)
            except TypeError:
                # An unhashable argument (a list of ids, say) -- don't
                # guess at an identity, just don't cache this call.
                return fn(*args, **kwargs)

            # Stored on the request object itself -- see the module
            # docstring for why this, and not `g`, is the correct scope.
            req = request._get_current_object()
            store = getattr(req, "_fms_request_cache", None)
            if store is None:
                store = {}
                req._fms_request_cache = store
            if cache_key not in store:
                store[cache_key] = fn(*args, **kwargs)
            return store[cache_key]
        return wrapper
    return decorator
