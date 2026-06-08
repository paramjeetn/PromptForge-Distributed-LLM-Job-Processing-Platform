from pydantic import BaseModel


class PromptSchema(BaseModel):
    prompt_id: int
    prompt: str
