"""FormEcho — wraps a failed submission's request.form so the same
create/edit template can redisplay it exactly as if `item` were the real
ORM object, preserving everything the user typed. Fixes the "form clears
on validation error" bug: routes previously re-rendered with `item=None`
on any validation exception, discarding a potentially long form's worth of
input and forcing the user to start over just to fix one field.

Usage in a route:
    except (SomeValidationError,) as e:
        flash(str(e), "danger")
        item = FormEcho(request.form, branch=resolved_branch_or_none)
        return render_template("...", item=item, ...)

`.id` always returns None, so template guards like
`{% if item and item.id %}` (used to gate "only show this on existing
records", e.g. attachment panels) correctly stay hidden for a
still-unsaved, failed submission.
"""


class FormEcho:
    def __init__(self, form, **relations):
        self._form = form
        self._relations = relations

    def __getattr__(self, name):
        if name in self._relations:
            return self._relations[name]
        if name == "id":
            return None
        value = self._form.get(name)
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return value

    def __bool__(self):
        return True
