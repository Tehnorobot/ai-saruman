import os
from utils.yaml_utils import YamlReader
from data_schema.structure_templates import REPO_PROMPTS_PATH


class PromptHelper:
    # BASE_PROMPTS_DIR = "./resources/Prompts"
    BASE_PROMPTS_DIR = REPO_PROMPTS_PATH
    @staticmethod
    def load_tool_prompt(tool_prompt_file: str, prompt_name: str) -> str:
        """
        Load a tool prompt from a YAML file.

        Args:
            tool_prompt_file (str): Name of the tool prompt file (without extension).
            prompt_name (str): Name of the specific prompt within the file to load.

        Returns:
            str: The loaded prompt string.
        """
        yaml_path = os.path.join(PromptHelper.BASE_PROMPTS_DIR, f"{tool_prompt_file}.yml")
        prompt = YamlReader.read_yaml(yaml_path)[prompt_name]
        return prompt


if __name__ == "__main__":
    from tests.framework.test_helper import TestHelper

    TestHelper.announce_testing(__file__)

    insrtuctions = PromptHelper.load_tool_prompt("instructions", "nickname_analyzer_task")
    print("Nickname analyzer propmt:")
    print(insrtuctions)