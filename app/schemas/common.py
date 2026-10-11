from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer

# Money is a Decimal internally (no float rounding errors) but is sent to the frontend as a JSON number.
Money = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]
