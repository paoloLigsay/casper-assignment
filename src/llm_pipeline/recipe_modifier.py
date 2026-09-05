"""
Step 2: Recipe Modification

This module applies structured modifications to recipes using search-and-replace operations.
It takes ModificationObject instances and applies their edits to recipe ingredients and instructions.
"""

import copy
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from loguru import logger

from .models import (
    ModificationObject,
    ModificationEdit,
    Recipe,
    ChangeRecord,
)


class RecipeModifier:
    """Applies structured modifications to recipes using search-and-replace operations."""

    def __init__(self, similarity_threshold: float = 0.6, cross_list_threshold: float = 0.9):
        """
        Initialize the RecipeModifier.

        Args:
            similarity_threshold: Minimum similarity score for fuzzy matching (0-1)
            cross_list_threshold: Minimum similarity required in the *other* list before
                auto-correcting an edit whose declared `target` doesn't match anything -
                deliberately stricter than similarity_threshold, since this overrides what
                the LLM explicitly declared rather than just confirming a plausible line
        """
        self.similarity_threshold = similarity_threshold
        self.cross_list_threshold = cross_list_threshold
        logger.info(f"Initialized RecipeModifier with similarity threshold: {similarity_threshold}")

    def find_best_match(self, target: str, candidates: List[str]) -> Tuple[Optional[str], Optional[int], float]:
        """
        Find the best matching string in a list of candidates.

        Args:
            target: String to find
            candidates: List of strings to search in

        Returns:
            Tuple of (best_match, index, similarity_score)
        """
        if not candidates:
            return None, None, 0.0

        best_match = None
        best_index = None
        best_score = 0.0

        for i, candidate in enumerate(candidates):
            similarity = SequenceMatcher(None, target.lower(), candidate.lower()).ratio()
            if similarity > best_score:
                best_score = similarity
                best_match = candidate
                best_index = i

        if best_score >= self.similarity_threshold:
            return best_match, best_index, best_score
        else:
            return None, None, best_score

    def apply_edit(
        self,
        edit: ModificationEdit,
        ingredients: List[str],
        instructions: List[str],
    ) -> Tuple[List[str], List[str], List[ChangeRecord]]:
        """
        Apply a single edit, given both lists so a mislabeled `target` can be
        recovered via a high-confidence match in the other list instead of
        silently dropping a valid edit.

        Args:
            edit: The edit operation to apply
            ingredients: Current ingredients list
            instructions: Current instructions list

        Returns:
            Tuple of (ingredients, instructions, change_records)
        """
        modified_ingredients = copy.deepcopy(ingredients)
        modified_instructions = copy.deepcopy(instructions)
        change_records: List[ChangeRecord] = []

        lists_by_target = {
            "ingredients": modified_ingredients,
            "instructions": modified_instructions,
        }
        other_target = "instructions" if edit.target == "ingredients" else "ingredients"

        logger.debug(f"Applying {edit.operation} edit: find='{edit.find}'")

        active_target = edit.target
        match, index, score = self.find_best_match(edit.find, lists_by_target[edit.target])

        if not match:
            other_match, other_index, other_score = self.find_best_match(
                edit.find, lists_by_target[other_target]
            )
            if other_match and other_score >= self.cross_list_threshold:
                logger.warning(
                    f"Edit declared target='{edit.target}' but find={edit.find!r} matches "
                    f"{other_target} instead (similarity: {other_score:.2f}) - applying there."
                )
                active_target = other_target
                match, index, score = other_match, other_index, other_score
            else:
                logger.warning(
                    f"Could not find '{edit.find}' in {edit.target} (best similarity: {score:.2f})"
                )
                return modified_ingredients, modified_instructions, change_records

        active_content = lists_by_target[active_target]
        record_type = "ingredient" if active_target == "ingredients" else "instruction"

        if edit.operation == "replace":
            original_text = active_content[index]
            new_text = original_text.replace(edit.find, edit.replace or "")
            active_content[index] = new_text

            change_records.append(ChangeRecord(
                type=record_type, from_text=original_text, to_text=new_text, operation="replace",
            ))
            logger.info(f"Replaced '{edit.find}' with '{edit.replace}' (similarity: {score:.2f})")

        elif edit.operation == "add_after":
            if edit.add:
                active_content.insert(index + 1, edit.add)

                change_records.append(ChangeRecord(
                    type=record_type, from_text="", to_text=edit.add, operation="add",
                ))
                logger.info(f"Added '{edit.add}' after '{edit.find}' (similarity: {score:.2f})")
            else:
                logger.warning(f"add_after edit for '{edit.find}' is missing 'add' text")

        elif edit.operation == "remove":
            removed_text = active_content.pop(index)

            change_records.append(ChangeRecord(
                type=record_type, from_text=removed_text, to_text="", operation="remove",
            ))
            logger.info(f"Removed '{edit.find}' (similarity: {score:.2f})")

        return modified_ingredients, modified_instructions, change_records

    def apply_modification(
        self,
        recipe: Recipe,
        modification: ModificationObject
    ) -> Tuple[Recipe, List[ChangeRecord]]:
        """
        Apply a complete modification to a recipe.

        Args:
            recipe: Original recipe to modify
            modification: Structured modification to apply

        Returns:
            Tuple of (modified_recipe, all_change_records)
        """
        logger.info(f"Applying {modification.modification_type} with {len(modification.edits)} edits")

        # Deep copy the recipe
        modified_recipe = Recipe(
            recipe_id=f"{recipe.recipe_id}_modified",
            title=recipe.title,
            ingredients=copy.deepcopy(recipe.ingredients),
            instructions=copy.deepcopy(recipe.instructions),
            description=recipe.description,
            servings=recipe.servings,
            rating=recipe.rating
        )

        all_change_records = []

        # Apply each edit
        for edit in modification.edits:
            modified_recipe.ingredients, modified_recipe.instructions, change_records = (
                self.apply_edit(edit, modified_recipe.ingredients, modified_recipe.instructions)
            )
            all_change_records.extend(change_records)

        logger.info(f"Applied modification successfully: {len(all_change_records)} changes made")
        return modified_recipe, all_change_records

    def apply_modifications_batch(
        self,
        recipe: Recipe,
        modifications: List[ModificationObject]
    ) -> Tuple[Recipe, List[List[ChangeRecord]]]:
        """
        Apply multiple modifications to a recipe sequentially.

        Args:
            recipe: Original recipe to modify
            modifications: List of modifications to apply

        Returns:
            Tuple of (final_modified_recipe, list_of_change_records_per_modification)
        """
        current_recipe = recipe
        all_change_records = []

        logger.info(f"Applying {len(modifications)} modifications sequentially")

        for i, modification in enumerate(modifications):
            logger.info(f"Applying modification {i + 1}/{len(modifications)}: {modification.modification_type}")

            current_recipe, change_records = self.apply_modification(current_recipe, modification)
            all_change_records.append(change_records)

        logger.info(f"Applied all modifications. Final recipe has {len(current_recipe.ingredients)} ingredients and {len(current_recipe.instructions)} instructions")
        return current_recipe, all_change_records

    def validate_modification_safety(
        self,
        modification: ModificationObject,
        recipe: Recipe
    ) -> Tuple[bool, List[str]]:
        """
        Validate that a modification won't break the recipe.

        Args:
            modification: Modification to validate
            recipe: Recipe being modified

        Returns:
            Tuple of (is_safe, list_of_warnings)
        """
        warnings = []
        is_safe = True

        for edit in modification.edits:
            # Check if target content exists
            target_content = recipe.ingredients if edit.target == "ingredients" else recipe.instructions
            match, _, score = self.find_best_match(edit.find, target_content)

            if not match:
                warnings.append(f"Cannot find '{edit.find}' in {edit.target}")
                is_safe = False
            elif score < 0.8:
                warnings.append(f"Low similarity match for '{edit.find}' (score: {score:.2f})")

            # Check for required fields
            if edit.operation == "replace" and not edit.replace:
                warnings.append(f"Replace operation missing replacement text for '{edit.find}'")
                is_safe = False
            elif edit.operation == "add_after" and not edit.add:
                warnings.append(f"Add operation missing text to add after '{edit.find}'")
                is_safe = False

        return is_safe, warnings