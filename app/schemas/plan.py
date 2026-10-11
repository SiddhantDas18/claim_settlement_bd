from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import Money


class CoverageRuleIn(BaseModel):
    category: str = Field(min_length=1, max_length=30)
    coverage_percent: Money = Field(ge=0, le=100, decimal_places=2)
    max_per_unit: Money | None = Field(default=None, ge=0, decimal_places=2)
    unit_label: str | None = Field(default=None, max_length=20)
    max_total: Money | None = Field(default=None, ge=0, decimal_places=2)


class CoverageRuleOut(CoverageRuleIn):
    model_config = ConfigDict(from_attributes=True)


class PlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    deductible: Money = Field(ge=0, decimal_places=2)
    annual_limit: Money = Field(gt=0, decimal_places=2)
    required_documents: list[str] = []
    is_active: bool = True
    coverage_rules: list[CoverageRuleIn] = []

    @model_validator(mode="after")
    def unique_categories(self) -> "PlanIn":
        categories = [rule.category for rule in self.coverage_rules]
        if len(categories) != len(set(categories)):
            raise ValueError("each category can only have one coverage rule")
        return self


class PlanOut(PlanIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    coverage_rules: list[CoverageRuleOut] = []


class PolicyCreate(BaseModel):
    policy_number: str = Field(min_length=1, max_length=50)
    user_id: int
    plan_id: int
    holder_name: str = Field(min_length=1, max_length=255)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def dates_in_order(self) -> "PolicyCreate":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class PolicyOut(PolicyCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
