import unittest

from shared.enums import TaskStatus
from shared.services.task_permissions import task_permissions, validate_status_transition


class TestTaskPermissions(unittest.TestCase):
    def test_new_to_in_progress_assigned_allowed(self):
        perms = task_permissions(
            status=TaskStatus.NEW.value,
            actor_user_id=10,
            created_by_user_id=1,
            assignee_user_ids=[10],
            started_by_user_id=None,
            is_admin=False,
            is_manager=False,
        )
        ok, code, _ = validate_status_transition(
            from_status=TaskStatus.NEW.value,
            to_status=TaskStatus.IN_PROGRESS.value,
            perms=perms,
            comment=None,
        )
        self.assertTrue(ok)
        self.assertEqual(code, 200)

    def test_in_progress_to_review_requires_executor(self):
        perms = task_permissions(
            status=TaskStatus.IN_PROGRESS.value,
            actor_user_id=10,
            created_by_user_id=1,
            assignee_user_ids=[11],
            started_by_user_id=None,
            is_admin=True,
            is_manager=False,
        )
        ok, code, msg = validate_status_transition(
            from_status=TaskStatus.IN_PROGRESS.value,
            to_status=TaskStatus.REVIEW.value,
            perms=perms,
            comment=None,
        )
        self.assertFalse(ok)
        self.assertEqual(code, 403)
        self.assertIn("прав", msg.lower())

    def test_review_to_done_only_admin_or_manager(self):
        perms_staff = task_permissions(
            status=TaskStatus.REVIEW.value,
            actor_user_id=10,
            created_by_user_id=1,
            assignee_user_ids=[10],
            started_by_user_id=None,
            is_admin=False,
            is_manager=False,
        )
        ok, code, _ = validate_status_transition(
            from_status=TaskStatus.REVIEW.value,
            to_status=TaskStatus.DONE.value,
            perms=perms_staff,
            comment=None,
        )
        self.assertFalse(ok)
        self.assertEqual(code, 403)

        perms_mgr = task_permissions(
            status=TaskStatus.REVIEW.value,
            actor_user_id=99,
            created_by_user_id=1,
            assignee_user_ids=[10],
            started_by_user_id=None,
            is_admin=False,
            is_manager=True,
        )
        ok, code, _ = validate_status_transition(
            from_status=TaskStatus.REVIEW.value,
            to_status=TaskStatus.DONE.value,
            perms=perms_mgr,
            comment=None,
        )
        self.assertTrue(ok)
        self.assertEqual(code, 200)

    def test_send_back_requires_comment(self):
        perms_mgr = task_permissions(
            status=TaskStatus.REVIEW.value,
            actor_user_id=99,
            created_by_user_id=1,
            assignee_user_ids=[10],
            started_by_user_id=None,
            is_admin=False,
            is_manager=True,
        )
        ok, code, msg = validate_status_transition(
            from_status=TaskStatus.REVIEW.value,
            to_status=TaskStatus.IN_PROGRESS.value,
            perms=perms_mgr,
            comment="",
        )
        self.assertFalse(ok)
        self.assertEqual(code, 400)
        self.assertIn("комментар", msg.lower())

    def test_archive_unarchive_only_admin_or_manager(self):
        perms_staff = task_permissions(
            status=TaskStatus.DONE.value,
            actor_user_id=10,
            created_by_user_id=1,
            assignee_user_ids=[10],
            started_by_user_id=None,
            is_admin=False,
            is_manager=False,
        )
        self.assertFalse(perms_staff.archive)

        perms_mgr = task_permissions(
            status=TaskStatus.DONE.value,
            actor_user_id=99,
            created_by_user_id=1,
            assignee_user_ids=[10],
            started_by_user_id=None,
            is_admin=False,
            is_manager=True,
        )
        self.assertTrue(perms_mgr.archive)

        perms_unarch_staff = task_permissions(
            status=TaskStatus.ARCHIVED.value,
            actor_user_id=10,
            created_by_user_id=1,
            assignee_user_ids=[10],
            started_by_user_id=None,
            is_admin=False,
            is_manager=False,
        )
        self.assertFalse(perms_unarch_staff.unarchive)

        perms_unarch_admin = task_permissions(
            status=TaskStatus.ARCHIVED.value,
            actor_user_id=1,
            created_by_user_id=1,
            assignee_user_ids=[10],
            started_by_user_id=None,
            is_admin=True,
            is_manager=False,
        )
        self.assertTrue(perms_unarch_admin.unarchive)


if __name__ == "__main__":
    unittest.main()
