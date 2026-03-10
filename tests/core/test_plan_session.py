from __future__ import annotations

from vibe.core.paths import PLANS_DIR
from vibe.core.plan_session import PlanSession


class TestPlanSession:
    def test_lazy_initialization(self) -> None:
        session = PlanSession()
        assert session._plan_file_path is None

    def test_stable_path(self) -> None:
        session = PlanSession()
        first = session.plan_file_path
        second = session.plan_file_path
        assert first == second

    def test_md_extension(self) -> None:
        session = PlanSession()
        assert session.plan_file_path.suffix == ".md"

    def test_name_format(self) -> None:
        session = PlanSession()
        stem = session.plan_file_path.stem
        parts = stem.split("-", 1)
        assert len(parts) == 2
        timestamp_str, slug = parts
        assert timestamp_str.isdigit()
        assert len(slug.split("-")) == 3

    def test_plan_file_path_str_matches(self) -> None:
        session = PlanSession()
        assert session.plan_file_path_str == str(session.plan_file_path)

    def test_path_under_plans_dir(self) -> None:
        session = PlanSession()
        assert session.plan_file_path.parent == PLANS_DIR.path

    def test_different_sessions_get_different_paths(self) -> None:
        session1 = PlanSession()
        session2 = PlanSession()
        assert session1.plan_file_path != session2.plan_file_path
