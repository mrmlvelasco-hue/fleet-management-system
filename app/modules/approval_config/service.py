"""Business rules for Approval Path and Approval Matrix maintenance,
including matrix resolution used by the runtime Approval Engine."""
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.modules.approval_config.models import ApprovalLevel
from app.modules.approval_config.repository import (
    ApprovalPathRepository, ApprovalMatrixRepository)


class InvalidPathError(Exception):
    pass


class MatrixOverlapError(Exception):
    pass


class NoMatrixError(Exception):
    pass


def _validate_levels(levels: list[dict]) -> None:
    if not levels:
        raise InvalidPathError("An approval path must have at least one level.")
    numbers = sorted(l["level_number"] for l in levels)
    if numbers != list(range(1, len(levels) + 1)):
        raise InvalidPathError(
            "Level numbers must be contiguous starting at 1.")
    for l in levels:
        if l.get("approver_type") == "ROLE" and not l.get("role_id"):
            raise InvalidPathError(
                f"Level {l['level_number']}: ROLE levels require a role.")
        if l.get("approver_type") == "USER" and not l.get("user_id"):
            raise InvalidPathError(
                f"Level {l['level_number']}: USER levels require a user.")


class ApprovalPathService:
    def __init__(self):
        self.repo = ApprovalPathRepository()

    def create(self, name, levels, description=None):
        _validate_levels(levels)
        path = self.repo.create(name=name, description=description)
        for l in levels:
            path.levels.append(ApprovalLevel(**l))
        db.session.commit()
        return path

    def update(self, path_id, *, name=None, description=None, levels=None):
        path = self.repo.get_by_id(path_id)
        if path is None:
            return None
        if name is not None:
            path.name = name
        if description is not None:
            path.description = description
        if levels is not None:
            _validate_levels(levels)
            path.levels.clear()
            db.session.flush()
            for l in levels:
                path.levels.append(ApprovalLevel(**l))
        db.session.commit()
        return path

    def deactivate(self, path_id):
        self.repo.soft_delete(path_id)
        db.session.commit()


def _ranges_overlap(a_min, a_max, b_min, b_max) -> bool:
    """Open bounds (None) mean -inf / +inf."""
    lo_a = a_min if a_min is not None else Decimal("-Infinity")
    hi_a = a_max if a_max is not None else Decimal("Infinity")
    lo_b = b_min if b_min is not None else Decimal("-Infinity")
    hi_b = b_max if b_max is not None else Decimal("Infinity")
    return lo_a <= hi_b and lo_b <= hi_a


def _dates_overlap(a_from, a_to, b_from, b_to) -> bool:
    lo_a = a_from or date.min
    hi_a = a_to or date.max
    lo_b = b_from or date.min
    hi_b = b_to or date.max
    return lo_a <= hi_b and lo_b <= hi_a


class ApprovalMatrixService:
    def __init__(self):
        self.repo = ApprovalMatrixRepository()

    def create(self, document_type_id, approval_path_id, min_amount=None,
               max_amount=None, effective_from=None, effective_to=None):
        self._check_overlap(document_type_id, min_amount, max_amount,
                            effective_from, effective_to, exclude_id=None)
        m = self.repo.create(
            document_type_id=document_type_id,
            approval_path_id=approval_path_id,
            min_amount=min_amount, max_amount=max_amount,
            effective_from=effective_from, effective_to=effective_to)
        db.session.commit()
        return m

    def update(self, matrix_id, **kwargs):
        m = self.repo.get_by_id(matrix_id)
        if m is None:
            return None
        candidate = {
            "document_type_id": kwargs.get("document_type_id", m.document_type_id),
            "min_amount": kwargs.get("min_amount", m.min_amount),
            "max_amount": kwargs.get("max_amount", m.max_amount),
            "effective_from": kwargs.get("effective_from", m.effective_from),
            "effective_to": kwargs.get("effective_to", m.effective_to),
        }
        self._check_overlap(candidate["document_type_id"],
                            candidate["min_amount"], candidate["max_amount"],
                            candidate["effective_from"], candidate["effective_to"],
                            exclude_id=matrix_id)
        for k, v in kwargs.items():
            setattr(m, k, v)
        db.session.commit()
        return m

    def deactivate(self, matrix_id):
        self.repo.soft_delete(matrix_id)
        db.session.commit()

    def _check_overlap(self, document_type_id, min_amount, max_amount,
                       effective_from, effective_to, exclude_id):
        for other in self.repo.list_for_document_type(document_type_id):
            if exclude_id is not None and other.id == exclude_id:
                continue
            if (_ranges_overlap(min_amount, max_amount,
                                other.min_amount, other.max_amount)
                    and _dates_overlap(effective_from, effective_to,
                                       other.effective_from, other.effective_to)):
                raise MatrixOverlapError(
                    "An approval matrix with an overlapping amount range and "
                    "effective period already exists for this document type.")

    def resolve(self, document_type_id, amount=None, on_date=None):
        """Return the ApprovalMatrix matching amount and date, else raise."""
        on_date = on_date or date.today()
        amount = Decimal(str(amount)) if amount is not None else None
        for m in self.repo.list_for_document_type(document_type_id):
            if m.effective_from and on_date < m.effective_from:
                continue
            if m.effective_to and on_date > m.effective_to:
                continue
            if amount is None:
                if m.min_amount is None and m.max_amount is None:
                    return m
                continue
            if m.min_amount is not None and amount < m.min_amount:
                continue
            if m.max_amount is not None and amount > m.max_amount:
                continue
            return m
        raise NoMatrixError(
            "No approval matrix matches this document type, amount and date.")
