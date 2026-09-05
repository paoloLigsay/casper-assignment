"""
Step 1: Tweak Extraction & Parsing

This module extracts structured modifications from review text using LLM processing.
It converts natural language descriptions of recipe changes into structured
ModificationObject instances.
"""

import json
import os
from typing import List, Optional

from loguru import logger
from openai import OpenAI
from pydantic import ValidationError

from .models import ModificationExtractionResult, ModificationObject, Recipe, Review
from .prompts import build_simple_prompt


class TweakExtractor:
    """Extracts structured modifications from review text using LLM processing."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-3.5-turbo"):
        """
        Initialize the TweakExtractor.

        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            model: OpenAI model to use for extraction
        """
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        self.model = model
        logger.info(f"Initialized TweakExtractor with model: {model}")

    def extract_modifications(
        self,
        review: Review,
        recipe: Recipe,
        max_retries: int = 2,
    ) -> List[ModificationObject]:
        """
        Extract all structured modifications described in a review.

        A single review can describe several discrete changes (e.g. "added an egg
        and halved the sugar"), so this returns a list rather than one modification.

        Args:
            review: Review object containing modification text
            recipe: Original recipe being modified
            max_retries: Number of retry attempts if parsing fails

        Returns:
            List of ModificationObject (empty if extraction failed)
        """
        if not review.has_modification:
            logger.warning("Review has no modification flag set")
            return []

        # Build the prompt - use simple prompt to avoid format string issues
        prompt = build_simple_prompt(
            review.text, recipe.title, recipe.ingredients, recipe.instructions
        )

        logger.debug(f"Extracting modifications from review: {review.text}")

        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.1,  # Low temperature for consistent extractions
                    max_tokens=1000,
                )

                raw_output = response.choices[0].message.content
                logger.debug(f"LLM raw output: {raw_output}")

                # Check if we got a response
                if not raw_output:
                    logger.warning(f"Attempt {attempt + 1}: Empty response from LLM")
                    continue

                # Parse and validate the JSON response
                modification_data = json.loads(raw_output)
                result = ModificationExtractionResult(**modification_data)

                types_list = "\n".join(
                    f"  {i}. {m.modification_type}"
                    for i, m in enumerate(result.modifications, 1)
                )
                logger.info(
                    f"Successfully extracted {len(result.modifications)} modification(s):\n{types_list}"
                )
                return result.modifications

            except json.JSONDecodeError as e:
                logger.warning(f"Attempt {attempt + 1}: Failed to parse JSON: {e}")
                if attempt == max_retries:
                    logger.error(f"Max retries reached. Raw output: {raw_output}")

            except ValidationError as e:
                logger.warning(f"Attempt {attempt + 1}: Validation error: {e}")
                if attempt == max_retries:
                    logger.error(
                        f"Max retries reached. Invalid data: {modification_data}"
                    )

            except Exception as e:
                logger.error(f"Attempt {attempt + 1}: Unexpected error: {e}")
                if attempt == max_retries:
                    return []

        return []

    def extract_modifications_from_review(
        self, reviews: list[Review], recipe: Recipe
    ) -> tuple[List[ModificationObject], Review] | tuple[List[ModificationObject], None]:
        """
        Extract modifications from a single randomly selected review.

        Args:
            reviews: List of reviews to choose from
            recipe: Original recipe being modified

        Returns:
            Tuple of (list_of_ModificationObject, source_Review). The list is empty
            and source_Review is None if no candidate review or extraction failed.
        """
        import random

        # Filter to reviews with modifications
        modification_reviews = [r for r in reviews if r.has_modification]

        if not modification_reviews:
            logger.warning("No reviews with modifications found")
            return [], None

        # Select one random review
        selected_review = random.choice(modification_reviews)
        # Debug override: force a specific review instead of random selection.
        # modification_reviews[1] is "These are awesome cookies..." for the
        # chocolate chip cookie recipe.
        # selected_review = modification_reviews[1]
        logger.info(f"Selected review:\n{selected_review.text}")

        modifications = self.extract_modifications(selected_review, recipe)
        if modifications:
            logger.info("Successfully extracted modifications from selected review")
            return modifications, selected_review
        else:
            logger.warning("Failed to extract modifications from selected review")
            return [], None

    def test_extraction(
        self, review_text: str, recipe_data: dict
    ) -> List[ModificationObject]:
        """
        Test extraction with raw text and recipe data.

        Args:
            review_text: Raw review text
            recipe_data: Raw recipe dictionary

        Returns:
            List of ModificationObject
        """
        review = Review(text=review_text, has_modification=True)
        recipe = Recipe(
            recipe_id=recipe_data.get("recipe_id", "test"),
            title=recipe_data.get("title", "Test Recipe"),
            ingredients=recipe_data.get("ingredients", []),
            instructions=recipe_data.get("instructions", []),
        )

        return self.extract_modifications(review, recipe)
