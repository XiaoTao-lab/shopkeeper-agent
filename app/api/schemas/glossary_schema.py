"""业务字典管理接口的请求体。响应直接复用 repository 的 glossary_view DTO。"""
from pydantic import BaseModel, Field, field_validator


class GlossaryCreate(BaseModel):
    term: str = Field(min_length=1, max_length=128)
    definition: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    guidance: str = Field(default="")

    @field_validator("term", "definition", "guidance")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("aliases")
    @classmethod
    def strip_aliases(cls, value: list[str]) -> list[str]:
        return [a.strip() for a in value if a.strip()]


class GlossaryUpdate(GlossaryCreate):
    enabled: bool = True
