"""The last approver should see 'Final Approve', not 'Approve'.

Requested because the last approval is materially different: it
completes the document and releases it for work, rather than passing it
to the next person. An approver who doesn't realise they're last can't
tell that their click is the one that matters.

is_final_level lives on ApprovalInstance and is the SAME value
ApprovalEngine.approve() uses to decide whether to finalise, so the
label can never promise something the engine won't do.
"""
import pytest

from app.core.approval.models import ApprovalInstance
from app.modules.approval_config.models import ApprovalPath, ApprovalLevel


def _path(db, name, levels):
    path = ApprovalPath(name=name)
    db.session.add(path)
    db.session.flush()
    for n in range(1, levels + 1):
        db.session.add(ApprovalLevel(path_id=path.id, level_number=n,
                                     approver_type="ROLE"))
    db.session.commit()
    return path


def _instance(db, path, level, ref):
    inst = ApprovalInstance(
        document_type_id=1,
        approval_path_id=path.id if path else None,
        reference_table="maintenance_orders", reference_id=ref,
        status="PENDING", current_level=level)
    db.session.add(inst)
    db.session.commit()
    return inst


def test_intermediate_level_is_not_final(db):
    path = _path(db, "Two Level", 2)
    assert _instance(db, path, 1, 1).is_final_level is False


def test_last_level_of_a_multi_level_path_is_final(db):
    path = _path(db, "Two Level B", 2)
    assert _instance(db, path, 2, 2).is_final_level is True


def test_single_level_path_is_final_immediately(db):
    """With one level, the only approver IS the final approver."""
    path = _path(db, "One Level", 1)
    assert _instance(db, path, 1, 3).is_final_level is True


def test_three_level_path_only_finalises_at_the_last(db):
    path = _path(db, "Three Level", 3)
    assert _instance(db, path, 1, 4).is_final_level is False
    assert _instance(db, path, 2, 5).is_final_level is False
    assert _instance(db, path, 3, 6).is_final_level is True


def test_missing_path_does_not_claim_finality(db):
    """A misconfigured instance must not show a button promising a
    finality it cannot deliver."""
    assert _instance(db, None, 1, 7).is_final_level is False


def test_path_with_no_levels_does_not_claim_finality(db):
    path = _path(db, "Empty Path", 0)
    assert _instance(db, path, 1, 8).is_final_level is False


def test_label_matches_what_the_engine_will_actually_do(db):
    """The guarantee that matters: wherever is_final_level is True, the
    engine's own max-level test agrees. If these ever diverged, the
    button would lie about the outcome."""
    path = _path(db, "Engine Agreement", 3)
    for level in (1, 2, 3):
        inst = _instance(db, path, level, 100 + level)
        engine_says_final = inst.current_level >= max(
            lvl.level_number for lvl in inst.approval_path.levels)
        assert inst.is_final_level == engine_says_final
