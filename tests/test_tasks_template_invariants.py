import os
import unittest


class TestTasksTemplateInvariants(unittest.TestCase):
    def _read(self, rel_path: str) -> str:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        path = os.path.join(root, rel_path)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def test_task_card_has_data_task_id(self):
        html = self._read("web/app/templates/tasks/board.html")
        self.assertIn('data-task-id="{{ t.id }}"', html)

    def test_quick_actions_container_does_not_swallow_clicks(self):
        html = self._read("web/app/templates/tasks/board.html")
        self.assertIn('class="task-quick-actions"', html)
        self.assertNotIn('data-role="task-quick-actions" onclick="event.stopPropagation()"', html)


if __name__ == "__main__":
    unittest.main()
