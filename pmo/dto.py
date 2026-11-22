from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Unavailable = Literal['UNAVAILABLE']
UNAVAILABLE: Unavailable = 'UNAVAILABLE'
Proportion = Annotated[str, Field(pattern=r'^(0\.[0-9]{6}|1\.000000)$')]


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')
