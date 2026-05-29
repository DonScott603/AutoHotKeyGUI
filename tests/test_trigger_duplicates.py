import unittest

from ahk_manager import Expansion, ExpansionStore


class TriggerDuplicateTests(unittest.TestCase):
    def test_case_variants_are_not_duplicates(self) -> None:
        store = ExpansionStore(
            sections=["Common"],
            expansions=[
                Expansion("Common", "Hsa", "Has"),
                Expansion("Common", "hsa", "has"),
            ],
        )

        self.assertEqual(store.duplicate_triggers(), {})

    def test_exact_same_trigger_is_duplicate(self) -> None:
        store = ExpansionStore(
            sections=["Common"],
            expansions=[
                Expansion("Common", "hsa", "has"),
                Expansion("Common", "hsa", "also has"),
            ],
        )

        duplicates = store.duplicate_triggers()

        self.assertIn("hsa", duplicates)
        self.assertEqual(len(duplicates["hsa"]), 2)


if __name__ == "__main__":
    unittest.main()
