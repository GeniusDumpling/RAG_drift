from pydantic import BaseModel, ConfigDict


class OrmBaseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
