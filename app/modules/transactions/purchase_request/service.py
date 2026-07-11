"""Purchase Request service: line-item management (with amount kept in
sync), the shared submit/approve/reject/return/cancel lifecycle (this is
the module that actually exercises the Approval Matrix's amount-range
resolution since PR carries a real monetary amount), plus mark_ordered/
mark_received physical-lifecycle actions."""
from app.extensions import db
from app.core.numbering.numbering_service import AutoNumberingService
from app.modules.transactions.base_service import BaseTransactionService
from app.modules.transactions.purchase_request.models import (
    PurchaseRequest, PurchaseRequestLine)


class LineManagementError(Exception):
    pass


def _recompute_amount(pr: PurchaseRequest) -> None:
    pr.amount = sum((line.line_total for line in pr.lines), start=0)


class PurchaseRequestService(BaseTransactionService):
    model = PurchaseRequest
    document_type_code = "PR"
    reference_table = "purchase_requests"

    def create(self, *, description, user, lines, department_id=None,
               vendor_id=None, justification=None, needed_by_date=None):
        numbering = AutoNumberingService()
        try:
            doc_number = numbering.generate(self.document_type_code)
        except Exception:
            doc_number = None

        pr = PurchaseRequest(
            document_number=doc_number, department_id=department_id,
            vendor_id=vendor_id, description=description,
            justification=justification, needed_by_date=needed_by_date,
            status="DRAFT", requested_by=user.id if user else None)
        db.session.add(pr)
        db.session.flush()

        for line in lines:
            qty = line["quantity"]
            cost = line["unit_cost"]
            pr.lines.append(PurchaseRequestLine(
                item_description=line["item_description"],
                quantity=qty, unit_cost=cost, line_total=qty * cost))
        _recompute_amount(pr)
        db.session.commit()
        return pr

    def _require_draft(self, pr: PurchaseRequest):
        if pr.approval_instance_id is not None or pr.status != "DRAFT":
            raise LineManagementError(
                "Line items can only be modified while the request is DRAFT "
                "and has not yet been submitted.")

    def add_line(self, pr_id: int, *, item_description, quantity, unit_cost):
        pr = db.session.get(PurchaseRequest, pr_id)
        self._require_draft(pr)
        pr.lines.append(PurchaseRequestLine(
            item_description=item_description, quantity=quantity,
            unit_cost=unit_cost, line_total=quantity * unit_cost))
        _recompute_amount(pr)
        db.session.commit()
        return pr

    def update_line(self, line_id: int, *, quantity=None, unit_cost=None,
                    item_description=None):
        line = db.session.get(PurchaseRequestLine, line_id)
        self._require_draft(line.request)
        if item_description is not None:
            line.item_description = item_description
        if quantity is not None:
            line.quantity = quantity
        if unit_cost is not None:
            line.unit_cost = unit_cost
        line.line_total = line.quantity * line.unit_cost
        _recompute_amount(line.request)
        db.session.commit()
        return line

    def remove_line(self, line_id: int):
        line = db.session.get(PurchaseRequestLine, line_id)
        pr = line.request
        self._require_draft(pr)
        pr.lines.remove(line)
        db.session.delete(line)
        _recompute_amount(pr)
        db.session.commit()

    def mark_ordered(self, pr_id: int):
        pr = db.session.get(PurchaseRequest, pr_id)
        pr.status = "ORDERED"
        db.session.commit()
        return pr

    def mark_received(self, pr_id: int):
        pr = db.session.get(PurchaseRequest, pr_id)
        pr.status = "RECEIVED"
        db.session.commit()
        return pr
