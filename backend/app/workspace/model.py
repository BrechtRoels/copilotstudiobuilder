from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Category = Literal[
    "agent", "settings", "connectionReferences", "topic", "action", "knowledge",
    "variable", "trigger", "childAgent", "workflow", "other",
]


class Component(BaseModel):
    id: str                       # relative path inside the workspace (POSIX), unique
    path: str
    category: Category
    kind: str | None = None
    name: str                     # stem / schema-ish name used for references
    displayName: str
    owner: str | None = None      # child agent folder name, None = main agent
    data: Any = None              # parsed content (plain)
    text: str = ""
    error: str | None = None      # parse error, if any


class WorkspaceInfo(BaseModel):
    id: str
    name: str
    path: str
    origin: Literal["generated", "cloned", "solution", "empty"] = "empty"
    createdAt: str
    description: str = ""


class Workspace(BaseModel):
    info: WorkspaceInfo
    components: list[Component] = Field(default_factory=list)
    connected: bool = False       # .mcs/conn.json present (cloned via VS Code extension)

    def by_category(self, cat: str) -> list[Component]:
        return [c for c in self.components if c.category == cat]

    def get(self, cid: str) -> Component | None:
        return next((c for c in self.components if c.id == cid), None)

    @property
    def agent(self) -> Component | None:
        return next((c for c in self.components if c.category == "agent"), None)

    @property
    def settings(self) -> dict:
        s = next((c for c in self.components if c.category == "settings"), None)
        return s.data if s and isinstance(s.data, dict) else {}

    def generative_orchestration(self) -> bool:
        s = self.settings
        flag = s.get("GenerativeActionsEnabled")
        if flag is None:
            flag = (s.get("settings") or {}).get("GenerativeActionsEnabled") if isinstance(s.get("settings"), dict) else None
        # New agents default to generative orchestration.
        return True if flag is None else bool(flag)


class FileChange(BaseModel):
    path: str
    content: str | None           # None = delete


class ChangeSet(BaseModel):
    summary: str = ""
    notes: list[str] = Field(default_factory=list)
    files: list[FileChange] = Field(default_factory=list)
