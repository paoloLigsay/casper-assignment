"""
Instruction Synthesizer - normalizes recipe instructions before modification.

RecipeModifier's remove/replace operations act on whole instruction list items.
A step that bundles two unrelated actions into one list item (e.g. "Dissolve
baking soda in hot water. Add to batter along with salt.") means removing
content for one action can silently delete the other. This module splits such
compound steps into atomic, self-contained steps before any extraction or
modification happens, so remove/replace can no longer take unrelated content
down with it.
"""

import json
import os
from typing import List, Optional

from loguru import logger
from openai import OpenAI
from pydantic import ValidationError

from .models import NormalizedInstructions
from .prompts import build_instruction_synthesis_prompt


class InstructionSynthesizer:
    """Splits compound recipe instruction steps into atomic, self-contained steps."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-3.5-turbo"):
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        self.model = model
        logger.info(f"Initialized InstructionSynthesizer with model: {model}")

    def synthesize(self, instructions: List[str], max_retries: int = 2) -> List[str]:
        """
        Split any instruction step that bundles multiple distinct actions into
        separate atomic steps.

        Args:
            instructions: Original instruction steps
            max_retries: Number of retry attempts if parsing fails

        Returns:
            Instructions with compound steps split, or the original list
            unchanged if synthesis fails after retries.
        """
        if not instructions:
            return instructions

        prompt = build_instruction_synthesis_prompt(instructions)

        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_tokens=1500,
                )

                raw_output = response.choices[0].message.content
                if not raw_output:
                    logger.warning(f"Attempt {attempt + 1}: Empty response from LLM")
                    continue

                data = json.loads(raw_output)
                result = NormalizedInstructions(**data)

                if len(result.instructions) != len(instructions):
                    logger.info(
                        f"Synthesizer split {len(instructions)} instruction(s) into "
                        f"{len(result.instructions)}"
                    )
                else:
                    logger.info("Synthesizer found no compound instructions to split")

                return result.instructions

            except json.JSONDecodeError as e:
                logger.warning(f"Attempt {attempt + 1}: Failed to parse JSON: {e}")
            except ValidationError as e:
                logger.warning(f"Attempt {attempt + 1}: Validation error: {e}")
            except Exception as e:
                logger.error(f"Attempt {attempt + 1}: Unexpected error: {e}")

        logger.warning(
            "Instruction synthesis failed after retries - using original instructions"
        )
        return instructions
