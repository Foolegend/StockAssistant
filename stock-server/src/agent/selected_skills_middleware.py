from typing import Any, Sequence

from deepagents.middleware.skills import SkillsMiddleware


class _SelectedSkillBackend:
    def __init__(self, backend: Any, directories_by_source: dict[str, set[str]]):
        self._backend = backend
        self._directories_by_source = directories_by_source

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)

    def ls(self, source_path: str) -> list[dict[str, Any]]:
        result = self._backend.ls(source_path)
        if hasattr(result, "error") and result.error:
            return result
        entries = result.entries if hasattr(result, "entries") else result
        selected_directories = self._directories_by_source.get(source_path, set())
        return [
            entry
            for entry in entries or []
            if entry.get("is_dir")
            and entry["path"].replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            in selected_directories
        ]


def filter_selected_skills(
    skills_metadata: list[dict[str, Any]],
    selected_skill_ids: Sequence[str],
) -> list[dict[str, Any]]:
    selected_ids = set(selected_skill_ids)
    return [skill for skill in skills_metadata if skill.get("name") in selected_ids]


class SelectedSkillsMiddleware(SkillsMiddleware):
    def __init__(
        self,
        backend: Any,
        sources: Sequence[str],
        selected_skill_ids: Sequence[str],
        directories_by_source: dict[str, Sequence[str]] | None = None,
    ):
        super().__init__(backend=backend, sources=list(sources))
        self.selected_skill_ids = tuple(selected_skill_ids)
        if directories_by_source is None:
            directories_by_source = {
                source: self.selected_skill_ids for source in self.sources
            }
        self._filtered_backend = _SelectedSkillBackend(
            backend,
            {
                source: set(directories)
                for source, directories in directories_by_source.items()
            },
        )

    def _get_backend(self, state, runtime, config):
        return self._filtered_backend

    def before_agent(self, state, runtime, config):
        update = super().before_agent(state, runtime, config)
        metadata = (
            update.get("skills_metadata", [])
            if update is not None
            else state.get("skills_metadata", [])
        )
        selected_metadata = filter_selected_skills(metadata, self.selected_skill_ids)

        if update is None:
            if selected_metadata == metadata:
                return None
            return {"skills_metadata": selected_metadata}
        return {**update, "skills_metadata": selected_metadata}
