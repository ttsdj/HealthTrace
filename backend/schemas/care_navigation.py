from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class RequestLocation(BaseModel):
    authorized: bool = False
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    accuracy_meters: float | None = Field(default=None, ge=0, le=100000)

    @model_validator(mode="after")
    def validate_authorized_coordinates(self):
        if self.authorized and (
            self.latitude is None or self.longitude is None
        ):
            raise ValueError("授权定位时必须同时提供 latitude 和 longitude")
        return self


class CareNavigationRequest(BaseModel):
    medical_need: str = Field(min_length=1, max_length=1000)
    location: RequestLocation
    radius_meters: int | None = Field(default=None, ge=1000, le=50000)


class CareNavigationResponse(BaseModel):
    status: str
    recommended_department: str
    urgency: str
    hospitals: list[dict] = Field(default_factory=list)
    message: str | None = None
    emergency_notice: str | None = None
    ranking_notice: str | None = None
    map_provider: str | None = None
    map_tool: str | None = None
    search_radius_meters: int | None = None
    hospital_count: int | None = None
