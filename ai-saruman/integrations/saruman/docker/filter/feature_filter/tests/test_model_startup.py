"""Startup contracts without downloading or running model weights."""

import unittest
from unittest.mock import Mock, patch

from src.model_filter import ModelFilter


class ModelStartupTests(unittest.TestCase):
    def test_shipped_mapping_and_only_production_models_are_loaded(self):
        model_filter = ModelFilter()
        self.assertEqual(len(model_filter.target_vaiables_id2topic_dict), 393)
        self.assertEqual(model_filter.target_vaiables_id2topic_dict["0"], "none")
        model = Mock()
        model.to.return_value = model
        model.config.num_labels = 393
        with (
            patch("src.model_filter.BertTokenizer.from_pretrained"),
            patch("src.model_filter.BertForSequenceClassification.from_pretrained", return_value=model) as load,
            patch.object(model_filter, "debug_new_topics_model", side_effect=AssertionError("unused model")),
        ):
            model_filter.load_models()
            model_filter.load_models()
        self.assertTrue(model_filter.models_loaded)
        self.assertEqual(
            [call.args[0] for call in load.call_args_list],
            ["apanc/russian-inappropriate-messages", "apanc/russian-sensitive-topics"],
        )
        self.assertEqual(model.eval.call_count, 2)

    def test_incompatible_mapping_cannot_be_marked_ready(self):
        model_filter = ModelFilter()
        model = Mock()
        model.to.return_value = model
        model.config.num_labels = 19
        with (
            patch("src.model_filter.BertTokenizer.from_pretrained"),
            patch("src.model_filter.BertForSequenceClassification.from_pretrained", return_value=model),
            self.assertRaisesRegex(ValueError, "mapping"),
        ):
            model_filter.load_models()
        self.assertFalse(model_filter.models_loaded)


if __name__ == "__main__":
    unittest.main()
