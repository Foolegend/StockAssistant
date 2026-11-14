import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agent.selected_skills_middleware import SelectedSkillsMiddleware, filter_selected_skills


class FakeBackend:
    def __init__(self):
        self.downloaded_paths = []

    def ls(self, source_path):
        return [
            {"path": f"{source_path}/stock-analysis", "is_dir": True},
            {"path": f"{source_path}/doc-handler", "is_dir": True},
        ]

    def download_files(self, paths):
        self.downloaded_paths.extend(paths)
        responses = []
        for path in paths:
            skill_id = path.replace("\\", "/").split("/")[-2]
            content = (
                f"---\nname: {skill_id}\ndescription: {skill_id} skill\n---\n"
            ).encode()
            responses.append(SimpleNamespace(error=None, content=content))
        return responses


class SelectedSkillsMiddlewareTests(unittest.TestCase):
    def test_filters_metadata_to_selected_skill_ids(self):
        skills = [
            {"name": "stock-analysis", "description": "Stocks"},
            {"name": "doc-handler", "description": "Documents"},
        ]

        selected = filter_selected_skills(skills, ["doc-handler"])

        self.assertEqual(selected, [{"name": "doc-handler", "description": "Documents"}])

    def test_empty_selection_exposes_no_skill_metadata(self):
        skills = [{"name": "stock-analysis", "description": "Stocks"}]

        self.assertEqual(filter_selected_skills(skills, []), [])

    def test_middleware_loads_only_selected_skill_from_shared_root(self):
        backend = FakeBackend()
        middleware = SelectedSkillsMiddleware(
            backend,
            ["/skills"],
            ["doc-handler"],
            {"/skills": ["doc-handler"]},
        )

        update = middleware.before_agent({}, None, {})

        self.assertEqual(
            [skill["name"] for skill in update["skills_metadata"]],
            ["doc-handler"],
        )
        self.assertEqual(
            backend.downloaded_paths,
            ["/skills/doc-handler/SKILL.md"],
        )
