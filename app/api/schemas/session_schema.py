"""Public inputs for session CRUD; responses use explicit repository DTOs."""
from pydantic import BaseModel, Field, field_validator


class SessionCreate(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("会话标题不能为空")
        return value


class SessionRename(SessionCreate):
    title: str = Field(min_length=1, max_length=100)
