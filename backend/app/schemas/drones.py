from pydantic import BaseModel


class DroneSummary(BaseModel):
    slug: str
    name: str
    manufacturer: str = "DJI"
    category: str
    status: str
    group_count: int
    parameter_count: int
    source_url: str
    collected_at: str
    note: str = ""


class DroneCatalog(BaseModel):
    items: list[DroneSummary]
    total: int
    parameter_count: int
    partial_count: int


class DroneParameter(BaseModel):
    name: str
    value: str


class DroneParameterGroup(BaseModel):
    name: str
    parameters: list[DroneParameter]


class DroneDetail(DroneSummary):
    groups: list[DroneParameterGroup]
