from pydantic import BaseModel, Field


class NotificationProbeRequest(BaseModel):
    channel: str = Field(pattern="^(webhook|email)$")
    recipient: str = Field(default="", max_length=320)
    confirm_external_delivery: bool = False

